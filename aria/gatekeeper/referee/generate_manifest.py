import os
import json
import hashlib
import hmac

def generate_manifest(key="mock_test_key"):
    base_dir = os.path.dirname(__file__)
    files_to_hash = ["server.py", "evaluators.py", "verifier.py", "scoring_config.json"]
    manifest = {}
    
    for f in files_to_hash:
        path = os.path.join(base_dir, f)
        if os.path.exists(path):
            with open(path, 'rb') as file:
                manifest[f] = hashlib.sha256(file.read()).hexdigest()
                
    signing_key = key.encode("utf-8")
    payload_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    signature = hmac.new(signing_key, payload_bytes, hashlib.sha256).hexdigest()
    
    manifest["signature"] = signature
    
    manifest_path = os.path.join(base_dir, "manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
        
    print(f"Generated manifest.json at {manifest_path}")

if __name__ == "__main__":
    generate_manifest()
