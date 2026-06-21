"""
aria/predictors/registry.py
───────────────────────────
Handles fetching active predictive models, promoting candidates, and rolling back.
"""

from __future__ import annotations
import sqlite3
import pickle
import logging
import threading
from typing import Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level cache to avoid repeated deserialization
_active_predictor_cache: dict[str, tuple[object, int]] = {}
_cache_lock = threading.Lock()

def get_active_predictor(predictor_type: str, db_path: str) -> Optional[Tuple[object, int]]:
    """
    Load the active model for `predictor_type` via pickle.
    Returns (pipeline, predictor_id) or None if no active predictor exists.
    """
    with _cache_lock:
        if predictor_type in _active_predictor_cache:
            return _active_predictor_cache[predictor_type]

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, model_path FROM predictor_registry WHERE predictor_type=? AND status='active'",
            (predictor_type,)
        ).fetchone()
        conn.close()

        if not row:
            return None

        predictor_id = row["id"]
        file_path = Path(row["model_path"])

        if not file_path.exists():
            logger.error(f"Predictor file missing: {file_path}")
            return None

        with open(file_path, "rb") as f:
            pipeline = pickle.load(f)

        with _cache_lock:
            _active_predictor_cache[predictor_type] = (pipeline, predictor_id)

        return pipeline, predictor_id
    except Exception as e:
        logger.error(f"Failed to load active predictor '{predictor_type}': {e}")
        return None

def promote_predictor(predictor_id: int, db_path: str) -> None:
    """
    Promote a 'candidate' predictor to 'active', retiring the currently active one.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT predictor_type, status FROM predictor_registry WHERE id=?", (predictor_id,)).fetchone()
        if not row:
            raise ValueError(f"Predictor {predictor_id} not found.")
        
        predictor_type = row["predictor_type"]
        
        # Retire current active
        conn.execute(
            "UPDATE predictor_registry SET status='retired' WHERE predictor_type=? AND status='active'",
            (predictor_type,)
        )
        
        # Activate new
        conn.execute(
            "UPDATE predictor_registry SET status='active' WHERE id=?",
            (predictor_id,)
        )
        conn.commit()
        
        with _cache_lock:
            if predictor_type in _active_predictor_cache:
                del _active_predictor_cache[predictor_type]
                
        logger.info(f"Promoted predictor {predictor_id} ({predictor_type}) to active.")
    finally:
        conn.close()

def rollback_predictor(predictor_type: str, db_path: str) -> None:
    """
    Retire the active predictor and promote the most recently created 'retired' predictor
    to active.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Find active
        active = conn.execute(
            "SELECT id FROM predictor_registry WHERE predictor_type=? AND status='active'",
            (predictor_type,)
        ).fetchone()
        
        if active:
            conn.execute("UPDATE predictor_registry SET status='retired' WHERE id=?", (active["id"],))
            
        # Find latest retired
        latest_retired = conn.execute(
            "SELECT id FROM predictor_registry WHERE predictor_type=? AND status='retired' ORDER BY created_at DESC LIMIT 1",
            (predictor_type,)
        ).fetchone()
        
        if latest_retired:
            conn.execute("UPDATE predictor_registry SET status='active' WHERE id=?", (latest_retired["id"],))
            conn.commit()
            
            with _cache_lock:
                if predictor_type in _active_predictor_cache:
                    del _active_predictor_cache[predictor_type]
            
            logger.info(f"Rolled back predictor {predictor_type} to ID {latest_retired['id']}.")
        else:
            conn.commit()
            logger.info(f"Retired active predictor for {predictor_type}, but no previous version to roll back to.")
    finally:
        conn.close()
