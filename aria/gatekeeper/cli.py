"""
aria/gatekeeper/cli.py
───────────────────────
Subprocess entrypoint for the Gatekeeper.

Receives candidate code from a file and runs StaticValidator and DockerSandbox.
Prints JSON output for the caller (Agent Core) to parse.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from aria.gatekeeper.sandbox import DockerSandbox
from aria.gatekeeper.validator import StaticValidator

def main():
    parser = argparse.ArgumentParser(description="ARIA Gatekeeper Subprocess")
    parser.add_argument("--tool", required=True, help="Name of the tool being evaluated")
    parser.add_argument("--source", required=True, help="Path to the candidate source code file")
    parser.add_argument("--raw-results-only", action="store_true", help="Return raw execution results without contacting Referee")
    parser.add_argument("--session-tests-file", help="Path to JSON file containing unsigned Tier 3 session tests")
    parser.add_argument("--session-token", help="HMAC signature of the session tests")
    parser.add_argument("--baseline-results-file", help="Path to JSON file containing raw baseline execution results")
    
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
        
    # Load extra arguments
    session_tests = None
    if args.session_tests_file:
        try:
            with open(args.session_tests_file, "r", encoding="utf-8") as f:
                session_tests = json.load(f)
        except Exception as e:
            print(json.dumps({"approved": False, "rejection_reason": f"Failed to load session tests: {e}"}))
            sys.exit(0)
            
    baseline_results = None
    if args.baseline_results_file:
        try:
            with open(args.baseline_results_file, "r", encoding="utf-8") as f:
                baseline_results = json.load(f)
        except Exception as e:
            print(json.dumps({"approved": False, "rejection_reason": f"Failed to load baseline results: {e}"}))
            sys.exit(0)

    sandbox = DockerSandbox()
    sandbox_result = asyncio.run(sandbox.run(
        tool_name=args.tool,
        candidate_source=candidate_source,
        current_stats=current_stats,
        raw_results_only=args.raw_results_only,
        session_tests=session_tests,
        session_token=args.session_token,
        baseline_results=baseline_results
    ))
    
    if args.raw_results_only:
        import dataclasses
        if dataclasses.is_dataclass(sandbox_result):
            print(json.dumps(dataclasses.asdict(sandbox_result)))
        else:
            print(json.dumps(sandbox_result))
    else:
        output = {
            "approved": sandbox_result.approved,
            "rejection_reason": sandbox_result.rejection_reason,
            "tests_passed": sandbox_result.tests_passed,
            "tests_total": sandbox_result.tests_total,
            "avg_latency_seconds": sandbox_result.avg_latency_seconds,
            "elapsed": time.monotonic() - start_time,
            "combat_report": sandbox_result.combat_report
        }
        print(json.dumps(output))

if __name__ == "__main__":
    main()
