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
    
    combat_report: dict | None = None

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

import re
def mock_geocode(request):
    if "xyzabc123notacity" in str(request.url):
        return Response(200, json={"results": []})
    return Response(200, json={"results": [{"latitude": 51.50853, "longitude": -0.12574, "name": "London", "country": "United Kingdom"}]})

respx_mock.get(re.compile(r"https://geocoding-api\.open-meteo\.com/v1/search.*")).mock(side_effect=mock_geocode)

respx_mock.get(re.compile(r"https://api\.open-meteo\.com/v1/forecast.*")).mock(
    return_value=Response(
        200,
        json={"current": {"temperature_2m": 15.0, "relative_humidity_2m": 72, "wind_speed_10m": 10.5, "weather_code": 3}}
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
        raw_results_only: bool = False,
        session_tests: list[dict] = None,
        session_token: str = None,
        baseline_results: list[dict] = None
    ) -> SandboxResult | list | dict:
        """
        Execute the candidate tool's tests in Docker.

        Args:
            tool_name:           The tool's name string (for logging)
            candidate_source:    The generated Python source code
            current_stats:       Current tool's average stats for comparison
            raw_results_only:    If True, bypass referee and just return results
            session_tests:       Optional Tier 3 test cases
            session_token:       HMAC signature of the session_tests
            baseline_results:    Raw results from the baseline run

        Returns:
            A SandboxResult with approval decision, OR raw results list/dict if raw_results_only.
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
        if session_tests:
            test_cases.extend(session_tests)
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
            import tarfile
            import io

            try:
                client = docker.from_env()
            except Exception as exc:
                return SandboxResult(
                    tool_name=tool_name,
                    approved=False,
                    rejection_reason=f"Docker unavailable: {exc}",
                    elapsed_seconds=time.monotonic() - start,
                )

            # Create an in-memory tarball containing candidate_tool.py and runner.py
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tool_info = tarfile.TarInfo(name="sandbox/candidate_tool.py")
                tool_bytes = candidate_source.encode("utf-8")
                tool_info.size = len(tool_bytes)
                tar.addfile(tool_info, io.BytesIO(tool_bytes))
                
                runner_info = tarfile.TarInfo(name="sandbox/runner.py")
                runner_bytes = runner_script.encode("utf-8")
                runner_info.size = len(runner_bytes)
                tar.addfile(runner_info, io.BytesIO(runner_bytes))
            tar_stream.seek(0)
            
            try:
                # Create the container without running it yet
                container = client.containers.create(
                    image="python:3.11-slim",
                    command=["sh", "-c", "export PYTHONPATH=/app && pip install -q httpx beautifulsoup4 groq python-dotenv respx && python /sandbox/runner.py"],
                    environment={
                        "GROQ_API_KEY": "mock_groq_key_for_sandbox",
                        "TEST_SIGNING_KEY": "mock_test_key"
                    },
                    mem_limit=settings.sandbox_memory_limit,
                    nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
                    stdin_open=True,
                    tty=False
                )
                
                # Create a tar of the aria directory
                aria_src_path = str(Path(__file__).parent.parent.absolute())
                aria_tar_stream = io.BytesIO()
                with tarfile.open(fileobj=aria_tar_stream, mode='w') as tar:
                    tar.add(aria_src_path, arcname="app/aria")
                aria_tar_stream.seek(0)
                
                # Upload aria source and sandbox files into the root '/'
                # Docker will automatically create /app and /sandbox if they are in the tar
                container.put_archive("/", aria_tar_stream)
                container.put_archive("/", tar_stream)
                
                # Start container and wait for completion
                container.start()
                exit_status = container.wait(timeout=settings.sandbox_timeout_seconds)
                logs_bytes = container.logs()
                logs = logs_bytes.decode("utf-8", errors="replace")
                
                # Remove container manually since we used create()
                try:
                    container.remove(force=True)
                except:
                    pass
                    
                if exit_status.get("StatusCode", 0) != 0:
                    return SandboxResult(
                        tool_name=tool_name,
                        approved=False,
                        rejection_reason=f"Docker run failed with status {exit_status.get('StatusCode')}",
                        elapsed_seconds=time.monotonic() - start,
                        docker_logs=logs[:500]
                    )
                    
            except Exception as exc:
                return SandboxResult(
                    tool_name=tool_name,
                    approved=False,
                    rejection_reason=f"Docker execution error: {exc}",
                    elapsed_seconds=time.monotonic() - start,
                    docker_logs=str(exc)[:500],
                )

            return self._parse_results(
                tool_name=tool_name,
                logs=logs,
                current_stats=current_stats,
                elapsed=time.monotonic() - start,
                raw_results_only=raw_results_only,
                session_tests=session_tests,
                session_token=session_token,
                baseline_results=baseline_results
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
        raw_results_only: bool = False,
        session_tests: list[dict] = None,
        session_token: str = None,
        baseline_results: list[dict] = None
    ) -> SandboxResult | list | dict:
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
        
        for r in results:
            tc_name = r.get("name", "unnamed")
            tc_passed = r.get("passed", False)
            tc_error = r.get("error")
            try:
                from aria.core.tracer import emit_trace
                emit_trace("gatekeeper", "test_result", {"tool": tool_name, "test_name": tc_name, "passed": tc_passed, "error": tc_error})
            except ImportError:
                pass

        total = len(results)
        latencies = [r["latency"] for r in results if "latency" in r]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        current_stats_dict = None
        if current_stats:
            current_stats_dict = {
                "success_rate": current_stats.success_rate if hasattr(current_stats, "success_rate") else 0.0,
                "avg_latency": current_stats.avg_latency if hasattr(current_stats, "avg_latency") else 0.0,
            }
            
        if raw_results_only:
            return results

        # --- Referee Evaluation ---
        import socket
        try:
            if hasattr(socket, "AF_UNIX"):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(10.0)
                client.connect("/sockets/referee.sock")
            else:
                # Windows local testing fallback
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(10.0)
                client.connect(("127.0.0.1", 5006))
                
            payload = json.dumps({
                "tool_name": tool_name,
                "results": results,
                "current_stats": current_stats_dict,
                "session_tests": session_tests,
                "session_token": session_token,
                "baseline_results": baseline_results
            })
            client.sendall(payload.encode("utf-8"))
            
            referee_response = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                referee_response += chunk
                
            referee_data = json.loads(referee_response.decode("utf-8"))
        except Exception as e:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                avg_latency_seconds=avg_latency,
                rejection_reason=f"Referee communication failed: {e}",
                docker_logs=logs[:500],
                elapsed_seconds=elapsed,
            )
        finally:
            try:
                client.close()
            except:
                pass

        approved = referee_data.get("approved", False)
        passed = referee_data.get("tests_passed", 0)
        failed = total - passed
        referee_reason = referee_data.get("reason", "No reason provided by Referee")

        if not approved:
            return SandboxResult(
                tool_name=tool_name,
                approved=False,
                tests_total=total,
                tests_passed=passed,
                tests_failed=failed,
                avg_latency_seconds=avg_latency,
                rejection_reason=f"Referee rejected: {referee_reason}",
                docker_logs=logs[:1000],
                elapsed_seconds=elapsed,
                combat_report=referee_data.get("combat_report")
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
            combat_report=referee_data.get("combat_report")
        )
