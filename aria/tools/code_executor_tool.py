"""
aria/tools/code_executor_tool.py
─────────────────────────────────
Safely executes Python code snippets inside a Docker container.

This tool delegates ALL execution to the Docker sandbox infrastructure —
it NEVER runs arbitrary code in the host Python process. If Docker is
unavailable, it fails safely rather than using subprocess/exec as a fallback.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import textwrap

from aria.tools.base import BaseTool, TestCase, ToolResult


class CodeExecutorTool(BaseTool):
    """
    Executes a Python code snippet and returns stdout/stderr.

    Input:
        code (str): Python source code to execute.
        timeout (int, optional): Execution timeout in seconds. Default: 10.
            Capped at SANDBOX_TIMEOUT_SECONDS from config.

    Output:
        A dict with 'stdout', 'stderr', 'exit_code', and 'timed_out' keys.
    """

    name = "code_executor_tool"

    def run(self, input: dict) -> ToolResult:
        code = str(input.get("code", "")).strip()
        timeout = min(int(input.get("timeout", 10)), 30)

        if not code:
            return ToolResult(success=False, output=None, error="No code provided.")

        # Quick static check — reject obviously dangerous patterns before Docker
        danger_patterns = [
            "import os", "import sys", "import subprocess",
            "import socket", "import shutil", "__import__",
            "open(", "eval(", "exec(",
        ]
        code_lower = code.lower().replace(" ", "")
        for pattern in danger_patterns:
            if pattern.replace(" ", "") in code_lower:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Rejected: code contains forbidden pattern '{pattern}'.",
                )

        try:
            result = self._run_in_docker(code, timeout)
            return ToolResult(success=True, output=result)
        except RuntimeError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"Executor error: {exc}")

    def _run_in_docker(self, code: str, timeout: int) -> dict:
        """Run code inside an isolated Docker container."""
        import docker  # type: ignore

        from aria.config import settings

        try:
            client = docker.from_env()
        except Exception as exc:
            raise RuntimeError(
                f"Docker is unavailable. Please ensure Docker Desktop is running. ({exc})"
            ) from exc

        # Wrap code so stdout is captured cleanly
        runner_code = textwrap.dedent(f"""
import sys, io
_stdout = io.StringIO()
_stderr = io.StringIO()
_orig_stdout, _orig_stderr = sys.stdout, sys.stderr
sys.stdout, sys.stderr = _stdout, _stderr
try:
    exec({code!r})
except Exception as _e:
    sys.stderr.write(str(_e))
finally:
    sys.stdout, sys.stderr = _orig_stdout, _orig_stderr
    print(_stdout.getvalue(), end='')
    print(_stderr.getvalue(), file=sys.stderr, end='')
""")

        try:
            container = client.containers.run(
                image="python:3.11-slim",
                command=["python", "-c", runner_code],
                mem_limit=settings.sandbox_memory_limit,
                nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
                network_disabled=True,
                remove=True,
                stdout=True,
                stderr=True,
                detach=False,
                timeout=timeout + 5,
            )

            if isinstance(container, bytes):
                stdout = container.decode("utf-8", errors="replace")
                stderr = ""
                exit_code = 0
            else:
                stdout = ""
                stderr = str(container)
                exit_code = 1

            return {
                "stdout": stdout[:2000],
                "stderr": stderr[:500],
                "exit_code": exit_code,
                "timed_out": False,
            }

        except Exception as exc:
            err = str(exc)
            timed_out = "timeout" in err.lower() or "timed out" in err.lower()
            return {
                "stdout": "",
                "stderr": err[:500],
                "exit_code": 1,
                "timed_out": timed_out,
            }

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="empty_code",
                input={"code": ""},
                expected_success=False,
                description="Empty code must fail.",
            ),
            TestCase(
                name="forbidden_import_os",
                input={"code": "import os; print(os.getcwd())"},
                expected_success=False,
                description="import os must be rejected before Docker.",
            ),
            TestCase(
                name="forbidden_eval",
                input={"code": "eval('1+1')"},
                expected_success=False,
                description="eval() must be rejected.",
            ),
            TestCase(
                name="safe_math",
                input={"code": "print(2 + 2)"},
                expected_success=True,
                description="Simple arithmetic print should succeed.",
            ),
        ]
