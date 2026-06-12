import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aria.main import bootstrap
from aria.core.agent import agent
from aria.metrics.db import get_tool_stats

def main():
    bootstrap()
    tool_name = "calculator_tool"
    
    expression = "floor(sqrt((abs(cos(pi)) + sin(pi/2) + log10(1000) + log2(16) + exp(tan(0))) / ((ceil(e) + floor(pi))/2)) * (factorial(5)/factorial(3) + 2^3))"
    print(f"Testing {tool_name} with expression: {expression}")
    
    before_stats = get_tool_stats(tool_name)
    result = agent.run_tool(tool_name, {"expression": expression})
    after_stats = get_tool_stats(tool_name)
    
    before_sr = f"{before_stats.success_rate:.0%}" if before_stats else "N/A"
    after_sr = f"{after_stats.success_rate:.0%}" if after_stats else "N/A"
    before_lat = f"{before_stats.avg_latency:.1f}s" if before_stats else "N/A"
    after_lat = f"{after_stats.avg_latency:.1f}s" if after_stats else "N/A"
    
    print("\n## Evaluation\n")
    print(f"Tool tested: {tool_name}\n")
    print(f"Success Rate:\nBefore: {before_sr}\nAfter: {after_sr}\n")
    print(f"Average Latency:\nBefore: {before_lat}\nAfter: {after_lat}\n")
    
    print("Result:")
    if result:
        print(f"{result.output if result.success else 'Error: ' + str(result.error)}\n")
    else:
        print("N/A\n")

if __name__ == "__main__":
    main()
