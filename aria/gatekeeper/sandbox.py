"""
aria/gatekeeper/sandbox.py
───────────────────────────
Docker sandbox executor — the second gate for generated tool code.

After a candidate tool passes static analysis, this module:
  1. Creates an isolated Docker container (no network, memory-limited)
  2. Injects the candidate tool source code + a test runner script
  3. Runs the tool's own test_cases() inside the container
  4. Collects pass/fail results and latency
  5. Returns a SandboxResult with a decision: APPROVE or REJECT

The sandbox compares new code performance against the current tool's
performance metrics from SQLite to ensure the improvement is genuine.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

from aria.config import settings


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    """
    The outcome of running a candidate tool's tests in Docker.
    """
    tool_name: str
    approved: bool

    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    avg_latency_seconds: float = 0.0

    rejection_reason: str | None = None
    docker_logs: str = ""
    elapsed_seconds: float = 0.0

    @property
    def pass_rate(self) -> float:
        if self.tests_total == 0:
            return 0.0
        return self.tests_passed / self.tests_total

    def summary(self) -> str:
        if self.approved:
            return (
                f"APPROVED — {self.tests_passed}/{self.tests_total} tests passed, "
                f"avg latency {self.avg_latency_seconds:.3f}s"
            )
        return f"REJECTED — {self.rejection_reason}"


# ── Test runner script ────────────────────────────────────────────────────────

_RUNNER_TEMPLATE = '''\
"""
Auto-generated test runner for ARIA sandbox validation.
Injected at runtime — do not edit.
"""
import json
import sys
import time
import importlib.util
import traceback

# ── Load the candidate tool module ──────────────────────────────────────────
spec = importlib.util.spec_from_file_location("candidate_tool", "/sandbox/candidate_tool.py")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(json.dumps({
        "error": f"Failed to import candidate tool: {e}",
        "traceback": traceback.format_exc(),
        "results": []
    }))
    sys.exit(1)

# ── Find the BaseTool subclass ───────────────────────────────────────────────
import inspect
tool_instance = None
for name, obj in inspect.getmembers(mod, inspect.isclass):
    bases = [b.__name__ for b in obj.__mro__]
    if "BaseTool" in bases and obj.__name__ != "BaseTool":
        try:
            tool_instance = obj()
        except Exception as e:
            print(json.dumps({"error": f"Cannot instantiate tool: {e}", "results": []}))
            sys.exit(1)
        break

if tool_instance is None:
    print(json.dumps({"error": "No BaseTool subclass found.", "results": []}))
    sys.exit(1)

# ── Run test cases ───────────────────────────────────────────────────────────
results = []
test_cases = tool_instance.test_cases()

for tc in test_cases:
    start = time.monotonic()
    try:
        result = tool_instance.run(tc["input"])
        latency = time.monotonic() - start
        
        success = result.success if hasattr(result, "success") else result.get("success", False)
        output = result.output if hasattr(result, "output") else result.get("output")
        
        expected_success = tc.get("expected_success", True)
        passed = (success == expected_success)
        
        # Check output_contains if specified
        if passed and tc.get("output_contains") and success:
            passed = tc["output_contains"] in str(output)
        
        results.append({
            "name": tc.get("name", "unnamed"),
            "passed": passed,
            "latency": latency,
            "success": success,
            "expected_success": expected_success,
            "error": result.error if hasattr(result, "error") else None,
        })
    except Exception as e:
        latency = time.monotonic() - start
        results.append({
            "name": tc.get("name", "unnamed"),
            "passed": False,
            "latency": latency,
            "error": str(e),
        })

output_data = {"results": results, "error": None}
print(json.dumps(output_data))
'''


# ── Sandbox executor ──────────────────────────────────────────────────────────

class DockerSandbox:
    """
    Runs candidate tool code inside an isolated Docker container.
    """

    def run(
        self,
        tool_name: str,
        candidate_source: str,
        current_avg_latency: float | None = None,
    ) -> SandboxResult:
        """
        Execute the candidate tool's tests in Docker.

        Args:
            tool_name:           The tool's name string (for logging)
            candidate_source:    The generated Python source code
            current_avg_latency: Current tool's average latency for comparison

        Returns:
            A SandboxResult with approval decision.
        """
        start = time.monotonic()

        # Prepare the runner script — convert TestCase dataclasses to plain dicts
        # so the runner can use them without importing ARIA internals
        runner_script = self._prepare_runner(candidate_source)
        if runner_script is None:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason="Failed to extract test cases from candidate code.",
                elapsed_seconds=time.monotonic() - start,
            )

        try:
            import docker  # type: ignore

            try:
                client = docker.from_env()
            except Exception as exc:
                return SandboxResult(
                    tool_name=tool_name,
                    approved=False,
                    rejection_reason=f"Docker unavailable: {exc}",
                    elapsed_seconds=time.monotonic() - start,
                )

            # Write files to a temp directory that we'll mount into the container
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                (tmp / "candidate_tool.py").write_text(candidate_source, encoding="utf-8")
                (tmp / "runner.py").write_text(runner_script, encoding="utf-8")

                # Also write the base.py so the runner can import BaseTool
                base_src = (
                    Path(__file__).parent.parent / "tools" / "base.py"
                ).read_text(encoding="utf-8")
                # Rewrite base.py without relative imports for standalone use
                base_src_standalone = base_src.replace(
                    "from aria.tools.base import", "# from aria.tools.base import"
                )
                (tmp / "base.py").write_text(base_src_standalone, encoding="utf-8")

                try:
                    container = client.containers.run(
                        image="python:3.11-slim",
                        command=["python", "/sandbox/runner.py"],
                        volumes={
                            str(tmp): {"bind": "/sandbox", "mode": "ro"}
                        },
                        mem_limit=settings.sandbox_memory_limit,
                        nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
                        network_disabled=True,
                        remove=True,
                        stdout=True,
                        stderr=True,
                        detach=False,
                        timeout=settings.sandbox_timeout_seconds + 5,
                    )
                    logs = container.decode("utf-8", errors="replace") if isinstance(container, bytes) else str(container)
                except Exception as exc:
                    return SandboxResult(
                        tool_name=tool_name,
                        approved=False,
                        rejection_reason=f"Docker run failed: {exc}",
                        elapsed_seconds=time.monotonic() - start,
                        docker_logs=str(exc)[:500],
                    )

            return self._parse_results(
                tool_name=tool_name,
                logs=logs,
                current_avg_latency=current_avg_latency,
                elapsed=time.monotonic() - start,
            )

        except ImportError:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason="Docker SDK not installed. Run: pip install docker",
                elapsed_seconds=time.monotonic() - start,
            )

    def _prepare_runner(self, candidate_source: str) -> str | None:
        """
        Build the runner script that will be injected into the container.
        The runner needs test case data as plain dicts (no ARIA imports in sandbox).
        We extract test cases by running the candidate code here first (static read),
        then embed them as JSON in the runner.
        """
        # For simplicity and security, use the universal runner template
        # which discovers test cases dynamically inside the sandbox
        return _RUNNER_TEMPLATE

    def _parse_results(
        self,
        tool_name: str,
        logs: str,
        current_avg_latency: float | None,
        elapsed: float,
    ) -> SandboxResult:
        """Parse Docker output and make an approval decision."""
        # Extract the last JSON line from logs (runner always prints JSON last)
        json_line = None
        for line in reversed(logs.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                json_line = line
                break

        if not json_line:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason="No JSON output from sandbox runner.",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
            )

        try:
            data = json.loads(json_line)
        except json.JSONDecodeError as exc:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason=f"Could not parse sandbox output: {exc}",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
            )

        if data.get("error"):
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason=f"Sandbox error: {data['error']}",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
            )

        results = data.get("results", [])
        total = len(results)
        passed = sum(1 for r in results if r.get("passed"))
        failed = total - passed
        latencies = [r["latency"] for r in results if "latency" in r]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        # Decision logic: ALL tests must pass
        if failed > 0:
            failed_names = [r["name"] for r in results if not r.get("passed")]
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                avg_latency_seconds=avg_latency,
                rejection_reason=f"{failed}/{total} tests failed: {', '.join(failed_names)}",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
            )

        # Performance regression check: new code must not be >50% slower
        if current_avg_latency and avg_latency > current_avg_latency * 1.5:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                avg_latency_seconds=avg_latency,
                rejection_reason=(
                    f"Performance regression: new avg latency {avg_latency:.3f}s is "
                    f">50% worse than current {current_avg_latency:.3f}s."
                ),
                elapsed_seconds=elapsed,
            )

        # All clear — approve
        return SandboxResult(
            tool_name=tool_name,
            approved=True,
            tests_total=total,
            tests_passed=passed,
            tests_failed=0,
            avg_latency_seconds=avg_latency,
            docker_logs=logs[:500],
            elapsed_seconds=elapsed,
        )
