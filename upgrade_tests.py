import os
import json
import hmac
import hashlib
from pathlib import Path

tests_dir = Path("c:/Users/Seratul Mustakim/Desktop/My Works/ARIA/aria/gatekeeper/tests")
signing_key = b"testing_key_1234"

def _compute_signature(tc: dict) -> str:
    payload = {
        "id": tc.get("id"),
        "tool": tc.get("tool"),
        "input": tc.get("input"),
        "expected_success": tc.get("expected_success", True),
        "output_contains": tc.get("output_contains"),
        "tier": tc.get("tier", "tier_1")
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hmac.new(signing_key, payload_bytes, hashlib.sha256).hexdigest()

def upgrade_tests():
    for filepath in tests_dir.glob("*_tests.json"):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        new_data = []
        for tc in data:
            # Default everything to tier_1 unless it's an adversarial/edge case
            # We can guess based on expected_success
            if "tier" not in tc:
                if not tc.get("expected_success", True):
                    tc["tier"] = "tier_3_adversarial"
                else:
                    tc["tier"] = "tier_1"
            
            # Special manual overrides for calculator
            if tc["id"] == "calculator_tool_003":
                tc["tier"] = "tier_2"
            if tc["id"] == "calculator_tool_004":
                tc["tier"] = "tier_2"
            
            # Resign
            tc["signature"] = _compute_signature(tc)
            new_data.append(tc)
            
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2)
            print(f"Upgraded {filepath.name}")

if __name__ == "__main__":
    upgrade_tests()
