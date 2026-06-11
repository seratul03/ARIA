"""
aria/introspection/clone.py
───────────────────────────
Manages isolated clones of the ARIA codebase for meta-improvement.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

from aria.config import settings

logger = logging.getLogger(__name__)

# The root of the ARIA project (where docker-compose.yml is)
ARIA_ROOT = Path(__file__).parent.parent.parent


class CloneManager:
    """Manages the creation and destruction of isolated ARIA clones."""

    def __init__(self) -> None:
        self.active_clones: Dict[str, Dict[str, Any]] = {}
        # Ensure the base directory exists
        settings.clone_base_dir.mkdir(parents=True, exist_ok=True)

    def create_clone(self) -> Tuple[str, str]:
        """
        Creates a fresh clone of the codebase.
        Returns:
            Tuple of (clone_dir, clone_id)
        """
        clone_id = str(uuid.uuid4())
        clone_dir = settings.clone_base_dir / f"aria_clone_{clone_id}"

        # We want a fresh copy without historical state
        def ignore_patterns(dir_path: str, contents: list[str]) -> list[str]:
            return [
                ".git",
                "__pycache__",
                ".venv",
                "aria.db",
                "aria.log",
                "audit.log",
                "self_model.json",
                "clones",  # Don't recursively copy clones
                "workspace", # Ignore workspace
                "data" # Ignore data
            ]

        shutil.copytree(ARIA_ROOT, clone_dir, ignore=ignore_patterns)

        # Patch the clone's config to use an isolated DB and self_model
        self._patch_clone_config(clone_dir, clone_id)

        # Record the clone
        self.active_clones[clone_id] = {
            "clone_id": clone_id,
            "clone_dir": str(clone_dir),
            "status": "running",  # Can be updated by the caller (e.g., to "failed")
            "container_id": None,  # Placeholder for when a container is launched
        }

        logger.info(f"[CloneManager] Created clone {clone_id} at {clone_dir}")
        return str(clone_dir), clone_id

    def _patch_clone_config(self, clone_dir: Path, clone_id: str) -> None:
        """Modify the .env file in the clone directory."""
        env_path = clone_dir / ".env"

        # If .env doesn't exist (e.g., ignored or absent), copy .env.example
        if not env_path.exists():
            example_path = clone_dir / ".env.example"
            if example_path.exists():
                shutil.copy(example_path, env_path)
            else:
                env_path.touch()

        # Read existing lines
        lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # Remove old DB_PATH and SELF_MODEL_PATH if they exist
        new_lines = [
            line
            for line in lines
            if not line.startswith("DB_PATH=") and not line.startswith("SELF_MODEL_PATH=")
        ]

        # Add new isolated paths.
        new_lines.append(f"\nDB_PATH=aria_{clone_id}.db\n")
        new_lines.append(f"SELF_MODEL_PATH=self_model_{clone_id}.json\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def run_evaluation(
        self, clone_id: str, command: str | list[str], timeout_seconds: int = 300
    ) -> Dict[str, Any]:
        """
        Runs an evaluation command inside the clone's isolated container.
        
        Args:
            clone_id: The ID of the clone.
            command: The command to execute (e.g., ["python", "-m", "aria", "evaluate"]).
            timeout_seconds: Maximum time the container is allowed to run.
            
        Returns:
            A dictionary containing the exit_code, logs, and whether it timed out.
        """
        record = self.active_clones.get(clone_id)
        if not record:
            return {"error": f"Clone {clone_id} not found."}

        clone_dir = Path(record["clone_dir"]).resolve()
        
        try:
            import docker  # type: ignore
            client = docker.from_env()
        except ImportError:
            return {"error": "Docker SDK not installed."}
        except Exception as e:
            return {"error": f"Docker unavailable: {e}"}

        try:
            # Run detached to manually monitor the timeout
            container = client.containers.run(
                image="aria-clone:latest",
                command=command,
                volumes={
                    str(clone_dir): {"bind": "/app", "mode": "rw"},
                    str(clone_dir / "aria"): {"bind": "/app/aria", "mode": "ro"},
                    str(clone_dir / "aria" / "improvement"): {"bind": "/app/aria/improvement", "mode": "rw"},
                    str(clone_dir / "aria" / "introspection"): {"bind": "/app/aria/introspection", "mode": "rw"},
                    str(clone_dir / "aria" / "ui"): {"bind": "/app/aria/ui", "mode": "rw"},
                    str(clone_dir / "aria" / "tools"): {"bind": "/app/aria/tools", "mode": "rw"},
                    str(clone_dir / "aria" / "core" / "scheduler.py"): {"bind": "/app/aria/core/scheduler.py", "mode": "rw"},
                    str(clone_dir / ".env"): {"bind": "/app/.env", "mode": "ro"},
                },
                network_mode="none",
                mem_limit="512m",
                nano_cpus=int(2 * 1e9),
                detach=True,
                remove=False,  # We manually remove it after fetching results
                stdout=True,
                stderr=True,
                working_dir="/app"
            )
            
            record["container_id"] = container.id
            
            # Wait for completion or timeout
            start_time = time.monotonic()
            timed_out = False
            
            try:
                while True:
                    container.reload()
                    if container.status == "exited":
                        break
                        
                    if time.monotonic() - start_time > timeout_seconds:
                        timed_out = True
                        container.kill()
                        break
                        
                    time.sleep(1.0)
                    
                logs = container.logs().decode("utf-8", errors="replace")
                
                if timed_out:
                    exit_code = -1
                    logs += f"\n\n[System] Container forcibly killed after {timeout_seconds}s timeout."
                else:
                    exit_code = container.attrs["State"]["ExitCode"]
                    
                return {
                    "exit_code": exit_code,
                    "logs": logs,
                    "timed_out": timed_out,
                    "error": None
                }
                
            finally:
                # Cleanup container after evaluation (dir remains)
                try:
                    container.remove(force=True)
                    record["container_id"] = None
                except Exception:
                    pass
                    
        except Exception as e:
            return {"error": f"Failed to run evaluation container: {e}"}

    def _remove_container(self, container_id: str | None) -> None:
        if not container_id:
            return
        try:
            import docker  # type: ignore

            client = docker.from_env()
            container = client.containers.get(container_id)
            container.remove(force=True)
            logger.info(f"[CloneManager] Removed container {container_id}")
        except Exception as e:
            logger.warning(f"[CloneManager] Failed to remove container {container_id}: {e}")

    def destroy_clone(self, clone_id: str, keep_on_failure: bool = False) -> None:
        record = self.active_clones.get(clone_id)
        if not record:
            logger.warning(f"[CloneManager] Clone {clone_id} not found for destruction.")
            return

        if keep_on_failure and record.get("status") == "failed":
            logger.info(f"[CloneManager] Keeping clone {clone_id} dir for debugging")
            # Still remove the container, just keep the filesystem
            self._remove_container(record.get("container_id"))
            return

        # Normal path — full cleanup
        self._remove_container(record.get("container_id"))
        shutil.rmtree(record.get("clone_dir"), ignore_errors=True)
        del self.active_clones[clone_id]
        logger.info(f"[CloneManager] Destroyed clone {clone_id}")


# Singleton instance
clone_manager = CloneManager()
