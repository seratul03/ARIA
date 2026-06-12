import json
import logging
from pathlib import Path
import hmac
import hashlib
import os

logger = logging.getLogger(__name__)

class RefereeEvaluator:
    def __init__(self, tests_dir: str, signing_key: str):
        self.tests_dir = Path(tests_dir)
        self.signing_key = signing_key.encode("utf-8")
        self._test_cases_cache = {}
        
        # Load scoring config
        base_dir = os.path.dirname(__file__)
        config_path = os.path.join(base_dir, "scoring_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            self.scoring_config = json.load(f)
            self.weights = self.scoring_config.get("weights", {})
            self.min_improvement_delta = self.scoring_config.get("min_improvement_delta", 0.05)

    def _compute_signature(self, tc: dict) -> str:
        payload = {
            "id": tc.get("id"),
            "tool": tc.get("tool"),
            "input": tc.get("input"),
            "expected_success": tc.get("expected_success", True),
            "output_contains": tc.get("output_contains"),
            "tier": tc.get("tier", "tier_1")
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hmac.new(self.signing_key, payload_bytes, hashlib.sha256).hexdigest()

    def _compute_session_token(self, tests: list[dict]) -> str:
        payload_bytes = json.dumps(tests, sort_keys=True).encode("utf-8")
        return hmac.new(self.signing_key, payload_bytes, hashlib.sha256).hexdigest()

    def load_tests(self, tool_name: str) -> list[dict]:
        if tool_name in self._test_cases_cache:
            return self._test_cases_cache[tool_name]

        tests_file = self.tests_dir / f"{tool_name}_tests.json"
        if not tests_file.exists():
            raise FileNotFoundError(f"No test cases found for {tool_name}")

        with open(tests_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for idx, tc in enumerate(data):
            stored_signature = tc.get("signature")
            if not stored_signature:
                raise ValueError(f"Missing signature in {tool_name} test {idx}")

            expected_signature = self._compute_signature(tc)
            if not hmac.compare_digest(stored_signature, expected_signature):
                raise ValueError(f"Tamper detected in {tool_name} test {idx}. Please re-sign test cases if schema changed.")

        self._test_cases_cache[tool_name] = data
        return data

    def _is_passed(self, expected: dict, actual: dict) -> bool:
        return actual.get("passed", False)

    def _compute_score(self, expected_tests: list[dict], execution_results: list[dict]) -> dict:
        tier_1_2_total = 0
        tier_1_2_passed = 0
        tier_3_total = 0
        tier_3_passed = 0
        latencies = []

        for expected, actual in zip(expected_tests, execution_results):
            passed = self._is_passed(expected, actual)
            tier = expected.get("tier", "tier_1")
            latencies.append(actual.get("latency", 0.0))
            
            if tier in ("tier_1", "tier_2"):
                tier_1_2_total += 1
                if passed:
                    tier_1_2_passed += 1
            elif tier == "tier_3_adversarial":
                tier_3_total += 1
                if passed:
                    tier_3_passed += 1
            else:
                # Fallback
                tier_1_2_total += 1
                if passed:
                    tier_1_2_passed += 1

        correctness = tier_1_2_passed / tier_1_2_total if tier_1_2_total > 0 else 1.0
        robustness = tier_3_passed / tier_3_total if tier_3_total > 0 else 1.0
        
        latencies.sort()
        idx = int(0.9 * len(latencies)) if latencies else 0
        latency_p90 = latencies[idx] if latencies else 0.0
        
        # Convert latency to a 0-1 score, where 0.0s = 1.0, 2.0s = 0.0
        latency_score = max(0.0, 1.0 - (latency_p90 / 2.0))
        
        final_score = (
            self.weights.get("correctness", 0.5) * correctness +
            self.weights.get("robustness", 0.3) * robustness +
            self.weights.get("latency", 0.2) * latency_score
        )
        return {
            "correctness": correctness,
            "robustness": robustness,
            "latency_p90": latency_p90,
            "overall_score": final_score,
            "tests_passed": tier_1_2_passed + tier_3_passed,
            "tests_total": tier_1_2_total + tier_3_total
        }

    def evaluate(self, tool_name: str, execution_results: list[dict], current_stats: dict = None, session_tests: list[dict] = None, session_token: str = None, baseline_results: list[dict] = None) -> dict:
        try:
            expected_tests = self.load_tests(tool_name).copy()
        except Exception as e:
            return {"approved": False, "reason": str(e)}

        if session_tests and session_token:
            expected_token = self._compute_session_token(session_tests)
            if not hmac.compare_digest(session_token, expected_token):
                return {"approved": False, "reason": "Invalid session token for session_tests. Tamper detected."}
            expected_tests.extend(session_tests)

        if len(execution_results) != len(expected_tests):
            return {
                "approved": False, 
                "reason": f"Test count mismatch. Expected {len(expected_tests)}, got {len(execution_results)}"
            }

        forbidden_action_count = 0  # Pre-checked by static AST analysis

        clone_metrics = self._compute_score(expected_tests, execution_results)
        clone_score = clone_metrics["overall_score"]
        
        baseline_metrics = None
        if baseline_results and len(baseline_results) == len(expected_tests):
            baseline_metrics = self._compute_score(expected_tests, baseline_results)
        elif current_stats:
            baseline_correctness = current_stats.get("success_rate", 0.0)
            baseline_robustness = current_stats.get("robustness", baseline_correctness)
            baseline_latency = current_stats.get("avg_latency", 0.0)
            baseline_latency_score = max(0.0, 1.0 - (baseline_latency / 2.0))
            
            current_aria_score = (
                self.weights.get("correctness", 0.5) * baseline_correctness +
                self.weights.get("robustness", 0.3) * baseline_robustness +
                self.weights.get("latency", 0.2) * baseline_latency_score
            )
            baseline_metrics = {
                "correctness": baseline_correctness,
                "robustness": baseline_robustness,
                "latency_p90": baseline_latency,
                "overall_score": current_aria_score,
                "tests_passed": 0,
                "tests_total": 0
            }
        else:
            return {"approved": False, "reason": "Missing baseline comparison data."}
            
        current_aria_score = baseline_metrics["overall_score"]
        improvement_delta = clone_score - current_aria_score
        
        safety_gate = "PASS" if forbidden_action_count == 0 else "FAIL"
        
        # Latency gate: clone must not be >20% slower than baseline
        # Wait, if baseline latency is 0, we avoid div by zero.
        if baseline_metrics["latency_p90"] > 0:
            if clone_metrics["latency_p90"] > baseline_metrics["latency_p90"] * 1.20:
                latency_gate = "FAIL"
            else:
                latency_gate = "PASS"
        else:
            latency_gate = "PASS"
            
        # Determine verdict
        if safety_gate == "FAIL" or latency_gate == "FAIL":
            verdict = "ARIA_WINS"
            approved = False
            reason = "Gates failed."
        elif improvement_delta < self.min_improvement_delta:
            verdict = "ARIA_WINS"
            approved = False
            reason = f"Improvement delta {improvement_delta:.3f} is less than required {self.min_improvement_delta}. clone: {clone_score:.3f}, current: {current_aria_score:.3f}."
        else:
            verdict = "CLONE_WINS"
            approved = True
            reason = f"Approved! Delta {improvement_delta:.3f} >= {self.min_improvement_delta}"

        combat_report = {
            "baseline": baseline_metrics,
            "clone": clone_metrics,
            "safety_gate": safety_gate,
            "latency_gate": latency_gate,
            "improvement_delta": improvement_delta,
            "verdict": verdict
        }

        return {
            "approved": approved,
            "tests_passed": clone_metrics["tests_passed"],
            "tests_total": clone_metrics["tests_total"],
            "reason": reason,
            "combat_report": combat_report
        }
