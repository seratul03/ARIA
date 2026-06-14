"""
aria/introspection/meta.py
───────────────────────────
Meta-introspection pass that analyzes recent execution traces and updates
the Self-Model schema with detected patterns and bottlenecks.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from pathlib import Path
import time

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.metrics.db import query_cycle_traces
from aria.introspection.self_model import self_model
from aria.introspection.clone import clone_manager

logger = logging.getLogger(__name__)

META_PROMPT = """You are analyzing the performance of an autonomous AI agent. 
Here are the execution traces from its last {n} improvement cycles. 
Identify recurring failure patterns, architectural bottlenecks, and specific weaknesses in each component. 

Return structured JSON exactly matching this schema:
{{
  "components": {{
    "improvement_engine": {{
      "known_weaknesses": ["list of strings"],
      "recent_failure_patterns": ["list of strings"]
    }},
    "gatekeeper": {{
      "known_weaknesses": [],
      "recent_failure_patterns": []
    }},
    "scheduler": {{
      "known_weaknesses": [],
      "recent_failure_patterns": []
    }},
    "introspection_engine": {{
      "known_weaknesses": [],
      "recent_failure_patterns": []
    }}
  }},
  "memory_summary": {{
    "active_patterns": 0,
    "resolved_patterns": 0,
    "top_recurring": []
  }},
  "system_wide_patterns": ["list of strings"]
}}

Only include components where you have identified actual weaknesses or patterns.
Output ONLY valid JSON.

Memory Summary (Current DB State):
{memory_summary_json}

Traces:
{traces_json}
"""

META_IMPROVEMENT_PROMPT = """You are ARIA's Meta-Improvement Engine. 
Your goal is to propose an architectural code change to fix weaknesses in your self-model.
Here is your current self-model:
{self_model_json}

You are allowed to modify exactly ONE of these files:
- aria/improvement/prompts.py
- aria/improvement/engine.py
- aria/introspection/meta.py
- aria/introspection/self_model.py
- aria/introspection/clone.py
- aria/core/scheduler.py
- aria/ui/cli.py

Return structured JSON exactly matching this schema:
{{
  "target_file": "path/to/file.py",
  "reasoning": "Explain why this change fixes the weakness",
  "new_content": "import ...\\n# Complete, updated file content here"
}}

Output ONLY valid JSON.
"""

def run_meta_introspection(n_cycles: int) -> None:
    """Run meta-introspection over the last N cycles and update the self-model."""
    
    # 0. Compress Memory
    try:
        from aria.memory.compression import compress_failure_history
        from aria.metrics.db import get_connection
        
        compress_failure_history()
        
        # Build memory summary
        with get_connection() as conn:
            active_patterns = conn.execute("SELECT COUNT(*) as c FROM failure_patterns WHERE status = 'active'").fetchone()["c"]
            resolved_patterns = conn.execute("SELECT COUNT(*) as c FROM failure_patterns WHERE status = 'resolved'").fetchone()["c"]
            top_recurring_rows = conn.execute("SELECT traceback_signature, tool_names, occurrence_count, status FROM failure_patterns WHERE status = 'active' ORDER BY occurrence_count DESC LIMIT 5").fetchall()
            
            top_recurring = []
            for r in top_recurring_rows:
                # Get the first tool name for simplicity or parse json
                try:
                    tools = json.loads(r["tool_names"])
                    tool_str = tools[0] if tools else "unknown"
                except Exception:
                    tool_str = r["tool_names"]
                    
                top_recurring.append({
                    "signature": r["traceback_signature"][:8],
                    "tool": tool_str,
                    "count": r["occurrence_count"],
                    "status": r["status"]
                })
                
        memory_summary = {
            "active_patterns": active_patterns,
            "resolved_patterns": resolved_patterns,
            "top_recurring": top_recurring
        }
    except Exception as e:
        logger.error(f"[MetaIntrospection] Failed to compress memory: {e}")
        memory_summary = {}
        
    # 1. Gather traces and update self-model
    try:
        traces = query_cycle_traces(limit=n_cycles)
        if not traces:
            logger.info("[MetaIntrospection] No traces available. Skipping.")
            return

        simplified_traces = []
        for t in traces:
            simplified_traces.append({
                "component": t.get("component"),
                "trigger": t.get("trigger"),
                "cycle_outcome": t.get("cycle_outcome"),
                "candidates_rejected": t.get("candidates_rejected"),
            })

        prompt = META_PROMPT.format(
            n=n_cycles, 
            traces_json=json.dumps(simplified_traces, indent=2),
            memory_summary_json=json.dumps(memory_summary, indent=2)
        )

        groq_limiter.acquire()
        
        try:
            from groq import Groq
        except ImportError:
            logger.error("[MetaIntrospection] Groq package not installed.")
            return

        client = Groq(api_key=settings.groq_api_key)
        
        logger.info(f"[MetaIntrospection] Analyzing last {len(traces)} traces...")
        
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": "You are an expert systems analyst AI. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=2000,
            temperature=0.2,
        )
        
        raw_output = response.choices[0].message.content or "{}"
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            logger.error("[MetaIntrospection] Failed to parse self-model JSON.")
            return
            
        # Merge into self_model
        if "components" in parsed:
            for comp, data in parsed["components"].items():
                for w in data.get("known_weaknesses", []):
                    self_model.add_weakness(comp, w)
                for p in data.get("recent_failure_patterns", []):
                    self_model.add_failure_pattern(comp, p)
                    
        for p in parsed.get("system_wide_patterns", []):
            self_model.add_system_pattern(p)
            
        if memory_summary:
            self_model.introspection_data["memory_summary"] = memory_summary
            
        self_model.save()
        logger.info("[MetaIntrospection] Self-model updated successfully.")
    
    except Exception as exc:
        logger.error(f"[MetaIntrospection] Failed during self-model update: {exc}")
        return

    # ── Phase 7.2: Rate Limits & Constraints ──
    # Max 1 concurrent clone at a time
    active = [c for c in clone_manager.active_clones.values() if c["status"] == "running"]
    if len(active) >= 1:
        logger.info("[MetaImprovement] Skipped: A clone is currently active.")
        return

    # Max 1 meta-improvement cycle per day (24 hours)
    meta_state_file = Path("meta_state.json")
    if meta_state_file.exists():
        try:
            state = json.loads(meta_state_file.read_text(encoding="utf-8"))
            last_run = state.get("last_run_time", 0)
            if time.time() - last_run < 86400:  # 24 hours
                logger.info("[MetaImprovement] Skipped: Rate limit reached (max 1 cycle/day).")
                return
        except Exception:
            pass

    # Record the start of a meta-cycle
    meta_state_file.write_text(json.dumps({"last_run_time": time.time()}), encoding="utf-8")

    # --- Clone Lifecycle ---
    
    # 2. Clone created from current ARIA snapshot
    clone_dir, clone_id = clone_manager.create_clone()
    
    try:
        # 3. LLM proposes architectural change
        logger.info("[MetaImprovement] Requesting architectural change from LLM...")
        groq_limiter.acquire()
        
        # We must manually inject a way to dump the self model, assuming `self_model.model_dump()` or similar exists.
        # Since self_model is an object, we serialize its attributes.
        sm_dict = getattr(self_model, "components", {})
        proposal_prompt = META_IMPROVEMENT_PROMPT.format(self_model_json=json.dumps(sm_dict, indent=2))
        
        proposal_response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": "You are ARIA's Meta-Improvement Engine. Output ONLY valid JSON."},
                {"role": "user", "content": proposal_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=4000,
            temperature=0.2,
        )
        
        proposal_raw = proposal_response.choices[0].message.content or "{}"
        try:
            proposal = json.loads(proposal_raw)
            target_file = proposal.get("target_file", "")
            new_content = proposal.get("new_content", "")
            reasoning = proposal.get("reasoning", "")
        except json.JSONDecodeError:
            logger.error("[MetaImprovement] Failed to parse proposal JSON.")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        if not target_file or not new_content:
            logger.error("[MetaImprovement] Proposal missing target_file or new_content.")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        # Validate target file is within allowed modifiable scope
        allowed_prefixes = ("aria/improvement/", "aria/introspection/", "aria/ui/", "aria/tools/", "aria/core/scheduler.py")
        if not target_file.startswith(allowed_prefixes) or ".." in target_file:
            logger.error(f"[MetaImprovement] Rejected proposal to modify forbidden file: {target_file}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        # ── Phase 7.3: The meta loop itself can NEVER be improved ──
        if target_file == "aria/introspection/meta.py":
            logger.error(f"[MetaImprovement] Rejected proposal: ARIA is forbidden from rewriting its own meta-loop ({target_file}).")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        # Validate Python syntax (Mitigate token truncation risk)
        try:
            import ast
            ast.parse(new_content)
        except SyntaxError as e:
            logger.error(f"[MetaImprovement] Discarding proposal due to SyntaxError in generated code (likely truncation): {e}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        logger.info(f"[MetaImprovement] Proposal accepted for {target_file}. Reasoning: {reasoning}")
        
        # 4. Change applied to clone only
        clone_file_path = Path(clone_dir) / target_file
        clone_file_path.parent.mkdir(parents=True, exist_ok=True)
        clone_file_path.write_text(new_content, encoding="utf-8")
        
        # 5. Clone runs evaluation suite 
        logger.info(f"[MetaImprovement] Running Arena Combat for clone {clone_id} on 'code_executor_tool'...")
        
        # We will use code_executor_tool as the benchmark since it's hard.
        benchmark_tool = "code_executor_tool"
        
        # We write a small evaluation script to run in the clone that triggers an improvement cycle
        # and captures the combat report result (specifically the clone's score vs current aria's score).
        eval_script_content = f"""
import sys
import json
from aria.main import bootstrap
bootstrap()
from aria.core.agent import agent
from aria.metrics.db import get_pending_reviews, get_tool_stats
import aria.core.tracer as tracer

# Ensure the benchmark tool has execution history, otherwise the cycle aborts
stats = get_tool_stats("{benchmark_tool}")
if not stats:
    if "{benchmark_tool}" == "code_executor_tool":
        agent.run_tool("{benchmark_tool}", {{"code": "print('hello')"}})
    elif "{benchmark_tool}" == "calculator_tool":
        agent.run_tool("{benchmark_tool}", {{"expression": "2+2"}})
    else:
        agent.run_tool("{benchmark_tool}", {{}})

# Disable actual deployments in the evaluation
agent._deploy = lambda *args, **kwargs: False

deployed = agent.run_improvement_cycle(target_tool="{benchmark_tool}")
reviews = get_pending_reviews()
for r in reviews:
    if r['tool_name'] == "{benchmark_tool}":
        print("COMBAT_REPORT:" + r['combat_report'])
        sys.exit(0)
print("COMBAT_REPORT_NOT_FOUND")
sys.exit(1)
"""
        eval_script_path = Path(clone_dir) / "run_meta_eval.py"
        eval_script_path.write_text(eval_script_content, encoding="utf-8")
        
        eval_result = clone_manager.run_evaluation(
            clone_id, 
            command=["python", "run_meta_eval.py"],
            timeout_seconds=600 # longer timeout because improvement takes time
        )
        
        # 6. Results returned to current ARIA
        if eval_result.get("error"):
            logger.error(f"[MetaImprovement] Evaluation failed to run: {eval_result['error']}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        logs = eval_result.get("logs", "")
        combat_report_json = None
        for line in logs.splitlines():
            if line.startswith("COMBAT_REPORT:"):
                try:
                    combat_report_json = json.loads(line.replace("COMBAT_REPORT:", "", 1))
                except json.JSONDecodeError:
                    pass
                break
                
        if not combat_report_json:
            logger.warning(f"[MetaImprovement] Evaluation FAILED (No combat report found). Logs: {logs[-500:]}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        b = combat_report_json.get("baseline", {})
        c = combat_report_json.get("clone", {})
        baseline_score = b.get("overall_score", 0.0)
        clone_score = c.get("overall_score", 0.0)
        delta = clone_score - baseline_score
        
        # The benchmark is hard, we require a meaningful delta to accept root changes
        min_meta_delta = 0.05 
        
        if delta >= min_meta_delta and combat_report_json.get("safety_gate") == "PASS":
            logger.info(f"[MetaImprovement] Evaluation PASSED! (Delta: {delta:.3f}) Queueing {target_file}...")
            
            # Create review queue entry for meta_improvement
            from aria.metrics.db import insert_review_queue
            insert_review_queue(
                session_id=clone_id,
                tool_name=target_file, # We use tool_name field for target_file here
                timestamp=time.time(),
                combat_report=json.dumps(combat_report_json),
                generated_code=new_content,
                status="pending"
            )
            
            if settings.require_human_review:
                logger.info(f"[MetaImprovement] Meta-improvement for {target_file} pending human review.")
            else:
                # 8. Decision: deploy
                import shutil
                from aria.versioning.git_manager import git_manager
                
                host_file_path = Path(__file__).parent.parent.parent / target_file
                host_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Tag pre-meta deployment
                git_manager.tag_commit(f"pre_meta_deployment_{int(time.time())}")
                
                shutil.copyfile(clone_file_path, host_file_path)
                
                commit_msg = f"Meta-improvement: {reasoning}"
                commit_hash = git_manager.commit_file(host_file_path, commit_msg)
                if commit_hash:
                    git_manager.tag_commit(f"post_meta_deployment_{int(time.time())}", commit_hash)
                
                from aria.memory.store import record_improvement
                record_improvement(
                    improvement_type='meta',
                    component_name=target_file,
                    problem_description=reasoning,
                    fix_summary=commit_msg,
                    result='deployed',
                    git_commit_hash=commit_hash,
                    baseline_fitness=baseline_score,
                    candidate_fitness=clone_score,
                )
                
                logger.info("[MetaImprovement] Deployment successful!")
                from aria.metrics.db import update_review_status, get_pending_reviews
                # Auto approve the review
                reviews = get_pending_reviews()
                for r in reviews:
                    if r['session_id'] == clone_id:
                        update_review_status(r['id'], "approved")
                clone_manager.active_clones[clone_id]["status"] = "success"
        else:
            logger.warning(f"[MetaImprovement] Evaluation FAILED. Delta {delta:.3f} < {min_meta_delta}. Discarding.")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            
    except Exception as exc:
        logger.error(f"[MetaImprovement] Failed during clone lifecycle: {exc}")
        clone_manager.active_clones[clone_id]["status"] = "failed"
        try:
            from aria.memory.store import record_failure
            import traceback
            record_failure(
                tool_name="meta",
                source="meta_clone",
                error_type=type(exc).__name__,
                error_message=str(exc),
                stack_trace=traceback.format_exc(),
            )
        except Exception:
            pass
    finally:
        # 7. Clone container destroyed
        clone_manager.destroy_clone(clone_id, keep_on_failure=settings.clone_keep_on_failure)


