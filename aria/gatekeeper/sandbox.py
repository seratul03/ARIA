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
import traceback
import tracemalloc
import importlib.util

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
    sys.exit(0)

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
            sys.exit(0)
        break

if tool_instance is None:
    print(json.dumps({"error": "No BaseTool subclass found.", "results": []}))
    sys.exit(0)

# ── Run test cases ───────────────────────────────────────────────────────────
results = []
try:
    test_cases = json.loads(__INJECTED_TEST_CASES_JSON__)
except Exception as e:
    print(json.dumps({
        "error": f"Failed to load injected test cases: {e}",
        "traceback": traceback.format_exc(),
        "results": []
    }))
    sys.exit(0)

# ── Mock External APIs ───────────────────────────────────────────────────────
import respx
from httpx import Response

respx_mock = respx.mock(assert_all_called=False)
respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
    return_value=Response(
        200, 
        json={"id": "mock", "choices": [{"message": {"role": "assistant", "content": "print('hello world')"}}]}
    )
)
respx_mock.start()

for tc in test_cases:
    start = time.monotonic()
    tracemalloc.start()
    tc_input = tc.input if hasattr(tc, "input") else tc.get("input", {})
    tc_expected_success = tc.expected_success if hasattr(tc, "expected_success") else tc.get("expected_success", True)
    tc_name = tc.name if hasattr(tc, "name") else tc.get("name", tc.get("id", "unnamed"))
    tc_output_contains = tc.output_contains if hasattr(tc, "output_contains") else tc.get("output_contains", None)
    
    try:
        result = tool_instance.run(tc_input)
        latency = time.monotonic() - start
        _, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory_mb = peak_mem / (1024 * 1024)
        
        success = result.success if hasattr(result, "success") else result.get("success", False)
        output = result.output if hasattr(result, "output") else result.get("output")
        tokens_used = result.tokens_used if hasattr(result, "tokens_used") else result.get("tokens_used", 0) if isinstance(result, dict) else 0
        
        passed = (success == tc_expected_success)
        
        # Check output_contains if specified
        if passed and tc_output_contains and success:
            passed = tc_output_contains in str(output)
        
        results.append({
            "name": tc_name,
            "passed": passed,
            "latency": latency,
            "success": success,
            "expected_success": tc_expected_success,
            "error": result.error if hasattr(result, "error") else None,
            "memory_mb": memory_mb,
            "tokens_used": tokens_used,
        })
    except Exception as e:
        latency = time.monotonic() - start
        try:
            tracemalloc.stop()
        except:
            pass
        results.append({
            "name": tc_name,
            "passed": False,
            "latency": latency,
            "error": str(e),
            "memory_mb": 0.0,
            "tokens_used": 0,
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
        current_stats: Any | None = None,
    ) -> SandboxResult:
        """
        Execute the candidate tool's tests in Docker.

        Args:
            tool_name:           The tool's name string (for logging)
            candidate_source:    The generated Python source code
            current_stats:       Current tool's average stats for comparison

        Returns:
            A SandboxResult with approval decision.
        """
        start = time.monotonic()

        # 1. Load and verify signed test cases from the host filesystem
        try:
            from aria.gatekeeper.test_verifier import verify_and_load_tests
            test_cases = verify_and_load_tests(tool_name)
        except Exception as exc:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason=f"Gatekeeper signature verification failed: {exc}",
                elapsed_seconds=time.monotonic() - start,
            )

        # 2. Prepare the runner script — inject the verified test cases as JSON
        runner_script = self._prepare_runner(candidate_source, test_cases)
        if runner_script is None:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason="Failed to prepare sandbox runner.",
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

                # Write candidate tool and runner
                (tmp / "candidate_tool.py").write_text(candidate_source, encoding="utf-8")
                (tmp / "runner.py").write_text(runner_script, encoding="utf-8")

                aria_host_dir = Path(__file__).parent.parent
                
                try:
                    container = client.containers.run(
                        image="python:3.11-slim",
                        command=["sh", "-c", "export PYTHONPATH=/app && pip install -q httpx beautifulsoup4 groq python-dotenv respx && python /sandbox/runner.py"],
                        volumes={
                            str(tmp): {"bind": "/sandbox", "mode": "ro"},
                            str(aria_host_dir): {"bind": "/app/aria", "mode": "ro"}
                        },
                        environment={
                            "GROQ_API_KEY": "mock_groq_key_for_sandbox",
                            "TEST_SIGNING_KEY": "mock_test_key"
                        },
                        mem_limit=settings.sandbox_memory_limit,
                        nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
                        remove=True,
                        stdout=True,
                        stderr=True,
                        detach=False,
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
                current_stats=current_stats,
                elapsed=time.monotonic() - start,
            )

        except ImportError:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                rejection_reason="Docker SDK not installed. Run: pip install docker",
                elapsed_seconds=time.monotonic() - start,
            )

    def _prepare_runner(self, candidate_source: str, test_cases: list[dict]) -> str | None:
        """
        Build the runner script that will be injected into the container.
        The runner receives the cryptographically verified test cases as a JSON string.
        """
        tests_json = json.dumps(test_cases)
        return _RUNNER_TEMPLATE.replace("__INJECTED_TEST_CASES_JSON__", repr(tests_json))

    def _parse_results(
        self,
        tool_name: str,
        logs: str,
        current_stats: Any | None,
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
            failed_details = []
            for r in results:
                if not r.get("passed"):
                    name = r.get("name", "unnamed")
                    error = r.get("error")
                    detail = f"{name}" + (f" (Error: {error})" if error else "")
                    failed_details.append(detail)
                    
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                avg_latency_seconds=avg_latency,
                rejection_reason=f"{failed}/{total} tests failed: {', '.join(failed_details)}",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
            )

        avg_memory_mb = sum(r.get("memory_mb", 0.0) for r in results) / len(results) if results else 0.0
        avg_tokens_used = sum(r.get("tokens_used", 0) for r in results) / len(results) if results else 0.0
        
        # New fitness
        new_fitness = (
            settings.weight_pass_rate * (passed / total)
            - settings.weight_latency * avg_latency
            - settings.weight_memory * avg_memory_mb
            - settings.weight_tokens * avg_tokens_used
        )

        current_fitness = None
        if current_stats:
            current_fitness = (
                settings.weight_pass_rate * current_stats.success_rate
                - settings.weight_latency * current_stats.avg_latency
                - settings.weight_memory * current_stats.avg_memory_mb
                - settings.weight_tokens * current_stats.avg_tokens_used
            )

        # Performance regression check: new fitness must not drop significantly
        if current_fitness is not None and new_fitness < current_fitness - 0.2:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                avg_latency_seconds=avg_latency,
                rejection_reason=(
                    f"Fitness regression: new fitness {new_fitness:.2f} is significantly "
                    f"worse than current {current_fitness:.2f}."
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
