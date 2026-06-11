import hmac
import hashlib
import json
import os
import sys

def verify_hashes():
    """
    Verify the sha256 hashes of the referee source code at startup using an HMAC signature.
    If there is a mismatch, the container crashes and refuses to serve.
    """
    base_dir = os.path.dirname(__file__)
    manifest_path = os.path.join(base_dir, 'manifest.json')
    
    if not os.path.exists(manifest_path):
        print("CRITICAL: manifest.json not found! Halting.")
        sys.exit(1)
        
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)
        
    signing_key = os.environ.get("TEST_SIGNING_KEY", "mock_test_key").encode("utf-8")
    
    # 1. Verify the signature of the manifest itself
    stored_signature = manifest.get("signature")
    if not stored_signature:
        print("CRITICAL: Manifest signature missing! Halting.")
        sys.exit(1)
        
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    expected_signature = hmac.new(signing_key, payload_bytes, hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(stored_signature, expected_signature):
        print("CRITICAL: Manifest signature invalid! Tampering detected. Halting.")
        sys.exit(1)
        
    # 2. Verify the hashes of the files
    for filename, expected_hash in payload.items():
        filepath = os.path.join(base_dir, filename)
        if not os.path.exists(filepath):
            print(f"CRITICAL: Missing file {filename}! Halting.")
            sys.exit(1)
            
        with open(filepath, 'rb') as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
            
        if not hmac.compare_digest(expected_hash, actual_hash):
            print(f"CRITICAL: Hash mismatch for {filename}! Tampering detected. Halting.")
            sys.exit(1)
            
    print("Verifier: All referee source files are authentic.")
