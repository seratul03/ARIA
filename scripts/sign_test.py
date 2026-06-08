#!/usr/bin/env python3
"""
scripts/sign_test.py
────────────────────
Utility to cryptographically sign a test case for ARIA's Gatekeeper.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to python path so we can import from aria
sys.path.insert(0, str(Path(__file__).parent.parent))

from aria.gatekeeper.test_verifier import _compute_signature

def main():
    parser = argparse.ArgumentParser(description="Sign a test case for ARIA Gatekeeper.")
    parser.add_argument("--tool", required=True, help="Tool name (e.g., calculator_tool)")
    parser.add_argument("--id", required=True, help="Test case ID (e.g., calc_001)")
    parser.add_argument("--input", required=True, help="JSON string for input")
    parser.add_argument("--expected-success", type=str, default="true", help="true or false")
    parser.add_argument("--output-contains", type=str, help="Optional string the output must contain")
    
    args = parser.parse_args()
    
    try:
        input_data = json.loads(args.input)
    except json.JSONDecodeError as exc:
        print(f"Error parsing --input JSON: {exc}")
        sys.exit(1)
        
    tc = {
        "id": args.id,
        "tool": args.tool,
        "input": input_data,
        "expected_success": args.expected_success.lower() == "true",
    }
    if args.output_contains:
        tc["output_contains"] = args.output_contains
        
    signature = _compute_signature(tc)
    tc["signature"] = signature
    
    # Load existing or create new
    tests_dir = Path(__file__).parent.parent / "aria" / "gatekeeper" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    tests_file = tests_dir / f"{args.tool}_tests.json"
    
    existing = []
    if tests_file.exists():
        with open(tests_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
            
    # Replace if id exists
    for i, existing_tc in enumerate(existing):
        if existing_tc.get("id") == tc["id"]:
            existing[i] = tc
            break
    else:
        existing.append(tc)
        
    with open(tests_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
        
    print(f"Successfully signed test '{tc['id']}' and saved to {tests_file}")

if __name__ == "__main__":
    main()
