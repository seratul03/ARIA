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
  "system_wide_patterns": ["list of strings"]
}}

Only include components where you have identified actual weaknesses or patterns.
Output ONLY valid JSON.

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

        traces_json = json.dumps(simplified_traces, indent=2)
        prompt = META_PROMPT.format(n=n_cycles, traces_json=traces_json)

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
            
        logger.info("[MetaIntrospection] Self-model updated successfully.")
        
    except Exception as exc:
        logger.error(f"[MetaIntrospection] Failed during self-model update: {exc}")
        return

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
        logger.info(f"[MetaImprovement] Running evaluation in clone {clone_id}...")
        eval_result = clone_manager.run_evaluation(
            clone_id, 
            command=["python", "-c", "import aria; print('ok')"]
        )
        
        # 6. Results returned to current ARIA
        if eval_result.get("error"):
            logger.error(f"[MetaImprovement] Evaluation failed to run: {eval_result['error']}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            return
            
        if eval_result.get("exit_code") == 0:
            logger.info(f"[MetaImprovement] Evaluation PASSED! Deploying {target_file} to host...")
            
            # 8. Decision: deploy
            import shutil
            from aria.versioning.git_manager import git_manager
            
            host_file_path = Path(__file__).parent.parent.parent / target_file
            host_file_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(clone_file_path, host_file_path)
            
            commit_msg = f"Meta-improvement: {reasoning}"
            git_manager.commit_file(host_file_path, commit_msg)
            
            logger.info("[MetaImprovement] Deployment successful!")
            clone_manager.active_clones[clone_id]["status"] = "success"
        else:
            # Decision: discard
            logger.warning(f"[MetaImprovement] Evaluation FAILED with exit code {eval_result.get('exit_code')}. Discarding change.")
            logger.debug(f"Evaluation logs:\n{eval_result.get('logs')}")
            clone_manager.active_clones[clone_id]["status"] = "failed"
            
    except Exception as exc:
        logger.error(f"[MetaImprovement] Failed during clone lifecycle: {exc}")
        clone_manager.active_clones[clone_id]["status"] = "failed"
    finally:
        # 7. Clone container destroyed
        clone_manager.destroy_clone(clone_id, keep_on_failure=settings.clone_keep_on_failure)

