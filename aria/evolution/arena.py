"""
aria/evolution/arena.py
───────────────────────
Parallel execution of candidate fixes through the Gatekeeper sandbox (Day 26).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aria.config import settings
from aria.metrics.db import get_connection

logger = logging.getLogger(__name__)

async def run_parallel_sandbox(
    candidates: list[dict],
    evolution_run_id: int,
    tool_name: str,
    db_path: str,
    emit_func=None
) -> list[dict]:
    """
    1. INSERT each candidate into evolution_candidates.
    2. Static analysis phase (sequential).
    3. Sandbox phase (parallel).
    4. UPDATE each evolution_candidates row with sandbox results.
    """
    from aria.gatekeeper.validator import StaticValidator

    # 1. Insert candidates into DB and tag with ID
    with get_connection() as conn:
        for c in candidates:
            cur = conn.execute(
                """
                INSERT INTO evolution_candidates 
                (evolution_run_id, strategy, source_code, fix_summary, static_analysis_passed)
                VALUES (?, ?, ?, ?, ?)
                """,
                (evolution_run_id, c["strategy"].value if hasattr(c["strategy"], "value") else c["strategy"], c["source_code"], c["fix_summary"], 0)
            )
            c["id"] = cur.lastrowid
            c["disqualified"] = 0
            c["disqualification_reason"] = None

    # 2. Sequential Static Analysis
    validator = StaticValidator()
    valid_candidates = []
    
    if emit_func:
        emit_func("STATIC_VALIDATION", "Running static validation on candidates...")
        
    for c in candidates:
        validation = validator.validate(c["source_code"], tool_name)
        if validation.passed:
            c["static_analysis_passed"] = 1
            with get_connection() as conn:
                conn.execute(
                    "UPDATE evolution_candidates SET static_analysis_passed = 1 WHERE id = ?",
                    (c["id"],)
                )
            valid_candidates.append(c)
        else:
            c["static_analysis_passed"] = 0
            c["disqualified"] = 1
            reason = f"Static validation failed: {'; '.join(validation.issues[:2])}"
            c["disqualification_reason"] = reason
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE evolution_candidates 
                    SET static_analysis_passed = 0, disqualified = 1, disqualification_reason = ?, static_analysis_issues = ?
                    WHERE id = ?
                    """,
                    (reason, json.dumps(validation.issues), c["id"])
                )
            logger.info(f"Candidate {c['id']} ({c['strategy']}) disqualified in static analysis: {reason}")

    if not valid_candidates:
        return candidates
        
    if emit_func:
        emit_func("STATIC_VALIDATION", "Generating session tests and running baseline...")

    # Prepare for Sandbox Phase (Generate session tests and run baseline sequentially)
    from aria.improvement.adversarial import AdversarialGenerator
    adv_gen = AdversarialGenerator()
    session_tests, session_token = adv_gen.generate_session_tests(tool_name)

    # Read current source for baseline
    tool_path = Path(__file__).parent.parent / "tools" / f"{tool_name}.py"
    try:
        current_source = tool_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read baseline source: {e}")
        for c in valid_candidates:
            c["disqualified"] = 1
            c["disqualification_reason"] = f"Baseline error: {e}"
        return candidates

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8", newline="") as temp_file_base, \
         tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8", newline="") as temp_session_tests, \
         tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8", newline="") as temp_baseline_res:
         
        temp_file_base.write(current_source)
        base_path = temp_file_base.name
        
        json.dump(session_tests, temp_session_tests)
        session_tests_path = temp_session_tests.name
        
        baseline_res_path = temp_baseline_res.name

    try:
        # Run baseline
        res_base_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "aria.gatekeeper.cli", 
            "--tool", tool_name, 
            "--source", base_path, 
            "--raw-results-only",
            "--session-tests-file", session_tests_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        stdout_bytes, _ = await res_base_proc.communicate()
        res_base_stdout = stdout_bytes.decode('utf-8', errors='replace')
        
        baseline_output = None
        for line in reversed(res_base_stdout.strip().splitlines()):
            if line.startswith("[") or line.startswith("{"):
                baseline_output = line
                break
                
        if not baseline_output:
            raise ValueError(f"Baseline run failed to return JSON: {res_base_stdout}")
            
        baseline_results = json.loads(baseline_output)
        if isinstance(baseline_results, dict) and not baseline_results.get("approved", True):
            raise ValueError(f"Baseline failed static validation: {baseline_results.get('rejection_reason')}")
            
        with open(baseline_res_path, "w", encoding="utf-8") as f:
            json.dump(baseline_results, f)
            
        # 3. Parallel Sandbox
        if emit_func:
            emit_func("SANDBOX_VALIDATION", f"Running {len(valid_candidates)} candidates in parallel sandbox...")
            
        # Create semaphores if we need to limit concurrency, otherwise asyncio handles it.
        # It's good to limit to settings.max_sandbox_workers to prevent out of memory
        semaphore = asyncio.Semaphore(settings.max_sandbox_workers)
        
        async def bound_run_sandbox(c):
            async with semaphore:
                return await run_sandbox_for_candidate(
                    c, tool_name, session_tests_path, session_token, baseline_res_path
                )
                
        tasks = [bound_run_sandbox(c) for c in valid_candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for c, result in zip(valid_candidates, results):
            if isinstance(result, Exception):
                c["disqualified"] = 1
                c["disqualification_reason"] = f"sandbox_error: {result}"
                c["sandbox_passed"] = 0
                
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE evolution_candidates SET disqualified = 1, disqualification_reason = ?, sandbox_passed = 0 WHERE id = ?",
                        (c["disqualification_reason"], c["id"])
                    )
            else:
                # Apply results to candidate
                c.update(result)
                
                with get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE evolution_candidates 
                        SET sandbox_passed = ?, baseline_fitness = ?, candidate_fitness = ?, 
                            fitness_delta = ?, test_pass_rate = ?, p90_latency_ms = ?, 
                            disqualified = ?, disqualification_reason = ?
                        WHERE id = ?
                        """,
                        (
                            c.get("sandbox_passed"), c.get("baseline_fitness"), c.get("candidate_fitness"),
                            c.get("fitness_delta"), c.get("test_pass_rate"), c.get("p90_latency_ms"),
                            c.get("disqualified"), c.get("disqualification_reason"), c["id"]
                        )
                    )
    finally:
        Path(base_path).unlink(missing_ok=True)
        Path(session_tests_path).unlink(missing_ok=True)
        Path(baseline_res_path).unlink(missing_ok=True)

    return candidates


async def run_sandbox_for_candidate(
    candidate: dict, 
    tool_name: str, 
    session_tests_path: str, 
    session_token: str, 
    baseline_res_path: str
) -> dict:
    """
    Thin wrapper calling gatekeeper CLI for a single candidate.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8", newline="") as temp_file_clone:
        temp_file_clone.write(candidate["source_code"])
        clone_path = temp_file_clone.name
        
    try:
        res_clone_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "aria.gatekeeper.cli", 
            "--tool", tool_name, 
            "--source", clone_path,
            "--session-tests-file", session_tests_path,
            "--session-token", session_token,
            "--baseline-results-file", baseline_res_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        stdout_bytes, _ = await res_clone_proc.communicate()
        res_clone_stdout = stdout_bytes.decode('utf-8', errors='replace')
        
        gatekeeper_output = None
        for line in reversed(res_clone_stdout.strip().splitlines()):
            if line.startswith("{"):
                gatekeeper_output = line
                break
                
        if not gatekeeper_output:
            raise RuntimeError(f"Gatekeeper output was empty: {res_clone_stdout}")
            
        sandbox_result = json.loads(gatekeeper_output)
        
        # Extract fitness values
        combat_report = sandbox_result.get("combat_report", {})
        baseline_score = 0.0
        candidate_score = 0.0
        p90_latency = 0.0
        
        if combat_report and "baseline" in combat_report and "clone" in combat_report:
            baseline_score = combat_report["baseline"].get("overall_score", 0.0)
            candidate_score = combat_report["clone"].get("overall_score", 0.0)
            p90_latency = combat_report["clone"].get("latency_p90", 0.0) * 1000.0
            
        pass_rate = 0.0
        if sandbox_result.get("tests_total", 0) > 0:
            pass_rate = sandbox_result.get("tests_passed", 0) / sandbox_result.get("tests_total", 1)
            
        approved = 1 if sandbox_result.get("approved", False) else 0
        
        return {
            "sandbox_passed": approved,
            "baseline_fitness": baseline_score,
            "candidate_fitness": candidate_score,
            "fitness_delta": candidate_score - baseline_score,
            "test_pass_rate": pass_rate,
            "p90_latency_ms": p90_latency,
            "disqualified": 0 if approved else 1,
            "disqualification_reason": sandbox_result.get("rejection_reason") if not approved else None,
            "combat_report": combat_report,
            "sandbox_result": sandbox_result
        }
    finally:
        Path(clone_path).unlink(missing_ok=True)
