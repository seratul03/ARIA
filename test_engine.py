import os
from dotenv import load_dotenv
load_dotenv()
from aria.improvement.engine import ImprovementEngine
import traceback

class DummyReport:
    def __init__(self):
        self.tool_name = "test"
        self.metrics = {"success": 0.0, "latency": 0.1, "failures": 1}
        self.code_context = "print('hello')"
        self.recent_failures = []
        self.memory_insights = ""
        self.reasons = []
        self.severity = "CRITICAL"
        self.fitness_score = -0.01
        self.success_rate = 0.0
        self.p90_latency = 0.1
        self.failure_count = 1
        self.total_executions = 1
        self.source_code = "print('hello')"

def main():
    engine = ImprovementEngine()
    try:
        result = engine.generate_improvement(DummyReport(), strategy="zero-shot")
        print("Success:", result.success)
        print("Error:", result.error)
    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    main()
