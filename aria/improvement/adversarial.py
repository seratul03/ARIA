"""
aria/improvement/adversarial.py
───────────────────────────────
Generates Tier-3 adversarial test cases dynamically for Arena Combat.
The LLM proposes the inputs ONLY. The expected outputs are computed
deterministically by reference implementations to avoid LLM hallucination
poisoning the test suite.
"""

import json
import logging
import uuid
import hmac
import hashlib
from typing import List, Dict, Any
from aria.config import settings

logger = logging.getLogger(__name__)

# ── Reference Implementations ──────────────────────────────────────────────────

def ref_calculator(input_data: dict) -> dict:
    expr = input_data.get("expression")
    if not expr or not isinstance(expr, str):
        return {"expected_success": False}
    
    # Very basic safety for our reference eval
    if any(c in expr for c in "import eval exec __"):
        return {"expected_success": False}
        
    try:
        # Use eval with empty globals/locals for safety
        result = eval(expr, {"__builtins__": {}}, {})
        return {
            "expected_success": True,
            "output_contains": str(result)
        }
    except Exception:
        return {"expected_success": False}

def ref_string_processor(input_data: dict) -> dict:
    op = input_data.get("operation")
    text = input_data.get("text")
    if not isinstance(text, str):
        return {"expected_success": False}
        
    if op == "reverse":
        return {
            "expected_success": True,
            "output_contains": text[::-1]
        }
    return {"expected_success": False}

def ref_fallback(input_data: dict) -> dict:
    """For tools without deterministic output, we just expect them to not crash."""
    return {"expected_success": True}

REFERENCE_IMPLS = {
    "calculator_tool": ref_calculator,
    "string_processor_tool": ref_string_processor,
}

# ── Generator ──────────────────────────────────────────────────────────────────

class AdversarialGenerator:
    def __init__(self):
        try:
            from groq import Groq
            self.client = Groq(api_key=settings.groq_api_key)
        except ImportError:
            self.client = None

    def generate_session_tests(self, tool_name: str) -> tuple[List[Dict[str, Any]], str]:
        """
        Generate adversarial test cases and sign them.
        Returns (test_cases, session_token).
        """
        if not self.client:
            logger.warning("Groq not installed, skipping adversarial generation.")
            return [], self._sign_tests([])

        # 1. Ask LLM to propose inputs
        prompt = (
            f"You are a QA engineer testing the '{tool_name}'. "
            "Generate 3 highly adversarial, edge-case, or tricky inputs for this tool. "
            "Return ONLY a JSON array of objects. Each object MUST have exactly one key: 'input' (a dictionary representing the tool's input parameters). "
            "Do not include any other text, markdown formatting, or expected outputs."
        )

        try:
            response = self.client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500
            )
            raw_content = response.choices[0].message.content or "[]"
            
            # Clean possible markdown
            import re
            match = re.search(r"\[.*\]", raw_content, re.DOTALL)
            if match:
                raw_content = match.group(0)
                
            inputs = json.loads(raw_content)
        except Exception as e:
            logger.error(f"Failed to generate adversarial inputs: {e}")
            inputs = []

        # 2. Compute expected outputs deterministically
        ref_func = REFERENCE_IMPLS.get(tool_name, ref_fallback)
        session_tests = []
        
        for item in inputs:
            if not isinstance(item, dict) or "input" not in item:
                continue
                
            tc_input = item["input"]
            expected = ref_func(tc_input)
            
            tc = {
                "id": str(uuid.uuid4()),
                "tool": tool_name,
                "tier": "tier_3_adversarial",
                "input": tc_input,
                "expected_success": expected.get("expected_success", True)
            }
            if "output_contains" in expected:
                tc["output_contains"] = expected["output_contains"]
                
            session_tests.append(tc)

        # 3. Sign the test cases to create session token
        token = self._sign_tests(session_tests)
        return session_tests, token

    def _sign_tests(self, tests: List[Dict[str, Any]]) -> str:
        """Create HMAC signature of the exact JSON payload."""
        key = settings.test_signing_key.encode("utf-8")
        # We must serialize deterministically to match what the Referee will receive
        payload_bytes = json.dumps(tests, sort_keys=True).encode("utf-8")
        return hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()
