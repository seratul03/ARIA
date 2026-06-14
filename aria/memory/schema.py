from __future__ import annotations

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def run_migrations(db_path: Path) -> None:
    """Run pending SQLite migrations from the migrations directory."""
    conn = sqlite3.connect(str(db_path))
    try:
        # Ensure schema_version table exists
        conn.execute('''
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        ''')
        
        # Get current version
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current_version = row[0] if row and row[0] is not None else 0
        
        migrations_dir = Path(__file__).parent / "migrations"
        
        if not migrations_dir.exists():
            return
            
        sql_files = sorted(migrations_dir.glob("*.sql"))
        
        for sql_file in sql_files:
            try:
                # Expect filenames like 001_initial.sql
                version = int(sql_file.name.split('_')[0])
                if version > current_version:
                    # If we're applying migration 1, handle the legacy table
                    if version == 1:
                        try:
                            conn.execute("ALTER TABLE improvement_history RENAME TO legacy_improvement_history")
                        except sqlite3.OperationalError:
                            pass # Table doesn't exist (fresh DB), safe to proceed

                    logger.info(f"Applying migration: {sql_file.name}")
                    with open(sql_file, "r", encoding="utf-8") as f:
                        script = f.read()
                        
                    with conn: # transaction
                        conn.executescript(script)
                        conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (version,))
                        
            except ValueError:
                logger.warning(f"Skipping incorrectly named migration file: {sql_file.name}")
            except Exception as e:
                logger.error(f"Migration {sql_file.name} failed: {e}")
                raise
    finally:
        conn.close()
