import os
import sys
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aria.main import bootstrap
from aria.core.agent import agent
from aria.metrics.db import get_tool_stats

def main():
    bootstrap()
    tool_name = "weather_tool"
    
    cities = ["New York", "Melbourne", "Monaco", "Kolkata", "London", "Sydney", "New Delhi", "Beijing"]
    city = random.choice(cities)
    
    print(f"Testing {tool_name} with city: {city}")
    
    before_stats = get_tool_stats(tool_name)
    result = agent.run_tool(tool_name, {"city": city, "units": "celsius"})
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
