"""
aria/core/audit.py
──────────────────
Client for the immutable Audit Log.
Sends UDP packets to the audit_logger container.
"""

import socket
import datetime
import json

AUDIT_HOST = "audit_logger"
AUDIT_PORT = 5005

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def log_audit_event(action: str, details: dict) -> None:
    """
    Send an event to the immutable audit log.
    action: e.g., 'IMPROVEMENT_ATTEMPT', 'DEPLOYMENT', 'ROLLBACK', 'REJECTION'
    details: dictionary of contextual info.
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    payload = {
        "timestamp": timestamp,
        "action": action,
        "details": details
    }
    
    message = json.dumps(payload)
    
    try:
        _sock.sendto(message.encode("utf-8"), (AUDIT_HOST, AUDIT_PORT))
    except Exception:
        # If the audit logger is down, we silently fail in this client so ARIA doesn't crash,
        # but in a stricter environment, we might want to block or panic.
        pass
