import json
import uuid
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from aria.gatekeeper.test_verifier import _compute_signature

def main():
    tool = "string_processor_tool"
    
    tests = [
        # Tier 1 (Public, known to ARIA)
        {"input": {"operation": "reverse", "text": "hello"}, "expected_success": True, "output_contains": "olleh", "tier": "tier_1"},
        {"input": {"operation": "reverse", "text": "world"}, "expected_success": True, "output_contains": "dlrow", "tier": "tier_1"},
        {"input": {"operation": "reverse", "text": "ARIA"}, "expected_success": True, "output_contains": "AIRA", "tier": "tier_1"},
        {"input": {"operation": "reverse", "text": "12345"}, "expected_success": True, "output_contains": "54321", "tier": "tier_1"},
        {"input": {"operation": "reverse", "text": "a b c"}, "expected_success": True, "output_contains": "c b a", "tier": "tier_1"},
        
        # Tier 2 (Private, hidden from ARIA)
        {"input": {"operation": "reverse", "text": "racecar"}, "expected_success": True, "output_contains": "racecar", "tier": "tier_2"},
        {"input": {"operation": "reverse", "text": "   spaces   "}, "expected_success": True, "output_contains": "   secaps   ", "tier": "tier_2"},
        {"input": {"operation": "reverse", "text": "A" * 1000}, "expected_success": True, "output_contains": "A" * 1000, "tier": "tier_2"},
        {"input": {"operation": "reverse", "text": "B" * 5000}, "expected_success": True, "output_contains": "B" * 5000, "tier": "tier_2"},
        {"input": {"operation": "reverse", "text": "!@#$%^&*()"}, "expected_success": True, "output_contains": ")(*&^%$#@!", "tier": "tier_2"},
        {"input": {"operation": "reverse", "text": "newline\n"}, "expected_success": True, "output_contains": "\nenilwen", "tier": "tier_2"},
        
        # Tier 3 (Adversarial, randomized edge cases)
        {"input": {"operation": "reverse", "text": ""}, "expected_success": True, "output_contains": "", "tier": "tier_3_adversarial"},
        {"input": {"operation": "reverse", "text": None}, "expected_success": False, "tier": "tier_3_adversarial"},
        {"input": {"operation": "reverse", "text": 12345}, "expected_success": False, "tier": "tier_3_adversarial"},
        {"input": {"operation": "reverse"}, "expected_success": False, "tier": "tier_3_adversarial"}, # missing text
        {"input": {"text": "hello"}, "expected_success": False, "tier": "tier_3_adversarial"}, # missing operation
        {"input": "not a dict", "expected_success": False, "tier": "tier_3_adversarial"},
        {"input": {"operation": "unknown", "text": "hello"}, "expected_success": False, "tier": "tier_3_adversarial"},
        {"input": {"operation": "reverse", "text": "<script>alert(1)</script>"}, "expected_success": True, "output_contains": ">tpircs/<>1(trela>tpircs<", "tier": "tier_3_adversarial"},
        {"input": {"operation": "reverse", "text": "eval('__import__(\"os\").system(\"ls\")')"}, "expected_success": True, "output_contains": ")'\"sl\"(metsys.)\"so\"(__tropmi__('lave", "tier": "tier_3_adversarial"},
    ]

    signed_tests = []
    for i, tc in enumerate(tests):
        tc_dict = {
            "id": f"{tool}_{str(uuid.uuid4())[:8]}",
            "tool": tool,
            "input": tc["input"],
            "expected_success": tc.get("expected_success", True),
            "tier": tc["tier"]
        }
        if "output_contains" in tc:
            tc_dict["output_contains"] = tc["output_contains"]
            
        tc_dict["signature"] = _compute_signature(tc_dict)
        signed_tests.append(tc_dict)

    tests_dir = Path(__file__).parent.parent / "aria" / "gatekeeper" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    tests_file = tests_dir / f"{tool}_tests.json"

    with open(tests_file, "w", encoding="utf-8") as f:
        json.dump(signed_tests, f, indent=2)

    print(f"Generated {len(signed_tests)} signed tests for {tool} in {tests_file}")

if __name__ == "__main__":
    main()
