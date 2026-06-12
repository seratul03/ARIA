import socket
import os
import sys
import json
import logging
from verifier import verify_hashes
from evaluators import RefereeEvaluator

logging.basicConfig(level=logging.INFO, format="[Referee] %(message)s")
logger = logging.getLogger(__name__)

SOCKET_PATH = os.environ.get("REFEREE_SOCKET_PATH", "/sockets/referee.sock")
TESTS_DIR = os.environ.get("TESTS_DIR", "/app/tests")
SIGNING_KEY = os.environ.get("TEST_SIGNING_KEY", "mock_test_key")

def run_server():
    # 1. Verify hashes first
    verify_hashes()

    # 2. Setup Evaluator
    evaluator = RefereeEvaluator(TESTS_DIR, SIGNING_KEY)

    # 3. Always bind TCP port 5006 so Windows hosts can reach us via port mapping.
    #    Also bind Unix socket if supported (for when sandbox runs in Docker too).
    servers = []

    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind(("0.0.0.0", 5006))
    tcp_server.listen(5)
    servers.append(tcp_server)
    logger.info("Referee listening on TCP 0.0.0.0:5006")

    if hasattr(socket, "AF_UNIX") and os.name != "nt":
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        unix_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_server.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)
        unix_server.listen(5)
        servers.append(unix_server)
        logger.info(f"Referee listening on Unix Socket: {SOCKET_PATH}")

    import select

    while True:
        readable, _, _ = select.select(servers, [], [])
        for server in readable:
            conn, _ = server.accept()
            try:
                data = conn.recv(1024 * 1024 * 5)  # Max 5MB payload
                if not data:
                    continue

                payload = json.loads(data.decode("utf-8"))
                tool_name = payload.get("tool_name")
                execution_results = payload.get("results", [])
                current_stats = payload.get("current_stats", None)
                session_tests = payload.get("session_tests", None)
                session_token = payload.get("session_token", None)
                baseline_results = payload.get("baseline_results", None)

                if not tool_name:
                    response = {"approved": False, "reason": "Missing tool_name in payload"}
                else:
                    response = evaluator.evaluate(tool_name, execution_results, current_stats, session_tests, session_token, baseline_results)

                conn.sendall(json.dumps(response).encode("utf-8"))
            except Exception as e:
                logger.error(f"Error handling request: {e}")
                try:
                    conn.sendall(json.dumps({"approved": False, "reason": f"Referee error: {e}"}).encode("utf-8"))
                except:
                    pass
            finally:
                conn.close()

if __name__ == "__main__":
    run_server()
