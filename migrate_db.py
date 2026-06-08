import sqlite3
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    db_path = Path("aria.db")
    if not db_path.exists():
        logger.info("aria.db does not exist, nothing to migrate.")
        return

    logger.info("Connecting to aria.db...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if columns exist first by attempting to select them, or just catch the error
        cursor.execute("SELECT memory_mb FROM tool_executions LIMIT 1")
        logger.info("Database is already migrated.")
    except sqlite3.OperationalError:
        logger.info("Applying migrations...")
        
        # Alter tool_executions
        cursor.execute("ALTER TABLE tool_executions ADD COLUMN memory_mb REAL DEFAULT 0.0;")
        cursor.execute("ALTER TABLE tool_executions ADD COLUMN tokens_used INTEGER DEFAULT 0;")
        
        # Alter improvement_history
        cursor.execute("ALTER TABLE improvement_history ADD COLUMN old_memory_mb REAL DEFAULT 0.0;")
        cursor.execute("ALTER TABLE improvement_history ADD COLUMN new_memory_mb REAL DEFAULT 0.0;")
        cursor.execute("ALTER TABLE improvement_history ADD COLUMN old_tokens_used INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE improvement_history ADD COLUMN new_tokens_used INTEGER DEFAULT 0;")
        
        conn.commit()
        logger.info("Migration completed successfully.")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
