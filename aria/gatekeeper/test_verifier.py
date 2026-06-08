"""
aria/gatekeeper/test_verifier.py
────────────────────────────────
Verifies cryptographically signed test cases for ARIA tools.
"""

from __future__ import annotations

import hmac
import hashlib
import json
from pathlib import Path

from aria.config import settings

def _compute_signature(tc: dict) -> str:
    """Compute HMAC-SHA256 signature for a test case."""
    key = settings.test_signing_key.encode("utf-8")
    
    # Extract only the data fields (ignore any existing signature)
    # We must sort keys to ensure stable serialization
    payload = {
        "id": tc.get("id"),
        "tool": tc.get("tool"),
        "input": tc.get("input"),
        "expected_success": tc.get("expected_success", True),
        "output_contains": tc.get("output_contains")
    }
    
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()

def verify_and_load_tests(tool_name: str) -> list[dict]:
    """
    Load test cases from the signed JSON file, verify signatures.
    Returns the raw test case dictionaries if valid.
    Raises ValueError if any signature is invalid or file missing.
    """
    tests_file = Path(__file__).parent / "tests" / f"{tool_name}_tests.json"
    
    if not tests_file.exists():
        raise FileNotFoundError(f"No test cases found for {tool_name} at {tests_file}")
        
    with open(tests_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    if not isinstance(data, list):
        raise ValueError("Test cases file must contain a JSON array.")
        
    for idx, tc in enumerate(data):
        stored_signature = tc.get("signature")
        if not stored_signature:
            raise ValueError(f"Test case {tc.get('id', idx)} is missing a signature.")
            
        expected_signature = _compute_signature(tc)
        
        # Constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(stored_signature, expected_signature):
            raise ValueError(f"Tamper detected! Invalid signature for test case {tc.get('id', idx)}.")
            
    return data
