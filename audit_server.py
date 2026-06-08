#!/usr/bin/env python3
"""
audit_server.py
───────────────
A lightweight UDP server that acts as an append-only audit log writer.
This server runs in its own Docker container and is the ONLY process
that mounts the physical `audit.log` file.
"""

import socket
import logging
import time

LOG_FILE = "/logs/audit.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(message)s",  # The client formats the message
)

def start_server(host="0.0.0.0", port=5005):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    
    print(f"Audit Server listening on udp://{host}:{port} -> {LOG_FILE}")
    
    while True:
        try:
            data, _ = sock.recvfrom(65535)
            message = data.decode("utf-8").strip()
            if message:
                # Append to the physical log file
                logging.info(message)
                # Flush to ensure it's written immediately
                for handler in logging.getLogger().handlers:
                    handler.flush()
        except Exception as e:
            print(f"Audit Server Error: {e}")

if __name__ == "__main__":
    start_server()
