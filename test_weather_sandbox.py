import os
import json
from aria.gatekeeper.sandbox import DockerSandbox
from pathlib import Path

def main():
    os.environ["SANDBOX_MEMORY_LIMIT"] = "128m"
    target = "weather_tool"
    tool_path = Path(f"aria/tools/{target}.py")
    source = tool_path.read_text(encoding="utf-8")
    
    sandbox = DockerSandbox()
    result = sandbox.run(
        tool_name=target,
        candidate_source=source,
        session_tests=[],
        session_token="abc",
        raw_results_only=True
    )
    
    if isinstance(result, dict):
        print("SANDBOX RESULT:", json.dumps(result, indent=2))
        if "docker_logs" in result:
            print("DOCKER LOGS:\n", result["docker_logs"])
    else:
        print("SUCCESS! Raw Results:")
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
