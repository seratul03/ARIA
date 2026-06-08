from aria.core.agent import agent
from aria.metrics.db import get_tool_stats
from aria.tools.registry import registry
from aria.tools.calculator_tool import CalculatorTool

registry.register(CalculatorTool())
print("Running tool...")
agent.run_tool("calculator_tool", {"expression": "100 + 200"})

print("Fetching stats...")
stats = get_tool_stats("calculator_tool")
print(f"Success Rate: {stats.success_rate}")
print(f"Latency: {stats.avg_latency}")
print(f"Memory (MB): {stats.avg_memory_mb}")
print(f"Tokens Used: {stats.avg_tokens_used}")

from aria.introspection.engine import IntrospectionEngine
engine = IntrospectionEngine()
report = engine.analyze_tool("calculator_tool")
print(f"Fitness Score: {report.fitness_score}")
