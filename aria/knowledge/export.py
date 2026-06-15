import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from aria.versioning.git_manager import git_manager

def export_rules_json(db_path: str, output_path: str = "engineering_rules.json") -> Dict[str, Any]:
    """
    1. SELECT * FROM engineering_rules ORDER BY category, status, confidence DESC.
    2. Build a deterministic JSON structure.
    3. Write to output_path.
    4. Call into Git Manager if content differs.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        try:
            rules_cursor = conn.execute(
                "SELECT * FROM engineering_rules ORDER BY category, status, confidence DESC"
            )
            rules = [dict(row) for row in rules_cursor.fetchall()]
        except sqlite3.OperationalError:
            rules = []
            
        try:
            version_row = conn.execute("SELECT version FROM knowledge_export_state WHERE id=1").fetchone()
            version = version_row["version"] if version_row else 1
        except sqlite3.OperationalError:
            version = 1
            
    # Read existing to see if it changed
    existing_rules = []
    existing_version = 0
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_rules = existing_data.get("rules", [])
                existing_version = existing_data.get("version", 0)
        except Exception:
            pass
            
    rules_json = json.dumps(rules, sort_keys=True)
    existing_rules_json = json.dumps(existing_rules, sort_keys=True)
    
    if rules_json != existing_rules_json:
        if existing_version >= version:
            version = existing_version + 1
            
        with sqlite3.connect(db_path) as conn:
            try:
                conn.execute("UPDATE knowledge_export_state SET version = ? WHERE id = 1", (version,))
            except sqlite3.OperationalError:
                pass
                
        export_data = {
            "version": version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rules": rules
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2)
            
        # Call Git Manager
        output_p = Path(output_path).resolve()
        git_manager.commit_file(
            file_path=output_p,
            message=f"knowledge: update engineering_rules.json (v{version})"
        )
    else:
        export_data = {
            "version": version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rules": rules
        }
        if not os.path.exists(output_path):
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)

    return export_data
