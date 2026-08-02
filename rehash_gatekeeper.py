import hashlib
import json
import os

gatekeeper_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aria", "gatekeeper")
manifest_path = os.path.join(gatekeeper_dir, "manifest.json")
files = ["validator.py", "sandbox.py", "test_verifier.py", "cli.py"]

manifest = {}
for filename in files:
    filepath = os.path.join(gatekeeper_dir, filename)
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha256.update(chunk)
    manifest[filename] = sha256.hexdigest()

with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print("Gatekeeper manifest updated.")
