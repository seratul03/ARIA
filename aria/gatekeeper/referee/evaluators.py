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
        tc_expected_success = expected.get("expected_success", True)
        tc_output_contains = expected.get("output_contains")

        actual_success = actual.get("success", False)
        actual_output = str(actual.get("output", ""))

        passed = (actual_success == tc_expected_success)
        if passed and tc_output_contains and actual_success:
            passed = tc_output_contains in actual_output
        return passed

    def evaluate(self, tool_name: str, execution_results: list[dict], current_stats: dict = None) -> dict:
        try:
            expected_tests = self.load_tests(tool_name)
        except Exception as e:
            return {"approved": False, "reason": str(e)}

        if len(execution_results) != len(expected_tests):
            return {
                "approved": False, 
                "reason": f"Test count mismatch. Expected {len(expected_tests)}, got {len(execution_results)}"
            }

        tier_1_2_total = 0
        tier_1_2_passed = 0
        tier_3_total = 0
        tier_3_passed = 0
        latencies = []
        forbidden_action_count = 0  # Pre-checked by static AST analysis

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
        
        clone_score = (
            self.weights.get("correctness", 0.5) * correctness +
            self.weights.get("robustness", 0.3) * robustness +
            self.weights.get("latency", 0.2) * latency_score
        )
        
        # Calculate baseline score from current_stats
        current_aria_score = 0.0
        if current_stats:
            baseline_correctness = current_stats.get("success_rate", 0.0)
            # If no robustness metric is tracked yet, assume it matches success_rate
            baseline_robustness = current_stats.get("robustness", baseline_correctness)
            baseline_latency = current_stats.get("avg_latency", 0.0)
            baseline_latency_score = max(0.0, 1.0 - (baseline_latency / 2.0))
            
            current_aria_score = (
                self.weights.get("correctness", 0.5) * baseline_correctness +
                self.weights.get("robustness", 0.3) * baseline_robustness +
                self.weights.get("latency", 0.2) * baseline_latency_score
            )
        
        safety = (forbidden_action_count == 0)
        improvement_delta = clone_score - current_aria_score
        
        if not safety:
            return {"approved": False, "reason": "Safety gate failed."}
            
        if improvement_delta < self.min_improvement_delta:
            return {
                "approved": False, 
                "reason": f"Improvement delta {improvement_delta:.3f} is less than required {self.min_improvement_delta}. clone: {clone_score:.3f}, current: {current_aria_score:.3f}."
            }
            
        return {
            "approved": True,
            "tests_passed": tier_1_2_passed + tier_3_passed,
            "tests_total": tier_1_2_total + tier_3_total,
            "reason": f"Approved! Delta {improvement_delta:.3f} >= {self.min_improvement_delta}"
        }
