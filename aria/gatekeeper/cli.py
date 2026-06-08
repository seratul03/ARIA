"""
aria/gatekeeper/cli.py
───────────────────────
Subprocess entrypoint for the Gatekeeper.

Receives candidate code from a file and runs StaticValidator and DockerSandbox.
Prints JSON output for the caller (Agent Core) to parse.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from aria.gatekeeper.sandbox import DockerSandbox
from aria.gatekeeper.validator import StaticValidator

def main():
    parser = argparse.ArgumentParser(description="ARIA Gatekeeper Subprocess")
    parser.add_argument("--tool", required=True, help="Name of the tool being evaluated")
    parser.add_argument("--source", required=True, help="Path to the candidate source code file")
    
    args = parser.parse_args()
    
    try:
        with open(args.source, "r", encoding="utf-8") as f:
            candidate_source = f.read()
    except Exception as exc:
        print(json.dumps({"approved": False, "rejection_reason": f"Failed to read source file: {exc}"}))
        sys.exit(0)
        
    start_time = time.monotonic()
    
    # 1. Static Validation
    validator = StaticValidator()
    validation = validator.validate(candidate_source, args.tool)
    
    if not validation.passed:
        reason = f"Static validation failed: {'; '.join(validation.issues[:2])}"
        print(json.dumps({"approved": False, "rejection_reason": reason, "elapsed": time.monotonic() - start_time}))
        sys.exit(0)
        
    # 2. Docker Sandbox Validation
    # We must fetch the current stats manually since we don't have db connection setup here,
    # wait, we can just let DockerSandbox fetch current_stats if it's not passed, or connect to DB.
    # It's cleaner if the caller doesn't pass current_stats, and DockerSandbox handles it, or 
    # we just connect to the DB here.
    try:
        from aria.metrics.db import get_tool_stats
        current_stats = get_tool_stats(args.tool)
    except Exception as e:
        print(json.dumps({"approved": False, "rejection_reason": f"Gatekeeper DB error: {e}"}))
        sys.exit(0)
        
    sandbox = DockerSandbox()
    sandbox_result = sandbox.run(
        tool_name=args.tool,
        candidate_source=candidate_source,
        current_stats=current_stats
    )
    
    output = {
        "approved": sandbox_result.approved,
        "rejection_reason": sandbox_result.rejection_reason,
        "tests_passed": sandbox_result.tests_passed,
        "tests_total": sandbox_result.tests_total,
        "avg_latency_seconds": sandbox_result.avg_latency_seconds,
        "elapsed": time.monotonic() - start_time
    }
    
    print(json.dumps(output))

if __name__ == "__main__":
    main()
