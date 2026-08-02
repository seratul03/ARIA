import hashlib
import json
import os
import hmac
from dotenv import load_dotenv

load_dotenv()

referee_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aria", "gatekeeper", "referee")
manifest_path = os.path.join(referee_dir, "manifest.json")
files = ["server.py", "evaluators.py", "verifier.py", "scoring_config.json"]

manifest = {}
for filename in files:
    filepath = os.path.join(referee_dir, filename)
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha256.update(chunk)
    manifest[filename] = sha256.hexdigest()

signing_key = os.environ.get("TEST_SIGNING_KEY", "testing_key_1234").encode("utf-8")

payload_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
signature = hmac.new(signing_key, payload_bytes, hashlib.sha256).hexdigest()

manifest["signature"] = signature

with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print("Referee manifest updated and signed.")
