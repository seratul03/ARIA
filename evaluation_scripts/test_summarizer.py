import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aria.main import bootstrap
from aria.core.agent import agent
from aria.metrics.db import get_tool_stats

def main():
    bootstrap()
    tool_name = "summarizer_tool"
    
    text = (
        "Artificial intelligence (AI) is intelligence demonstrated by machines, as opposed "
        "to the natural intelligence displayed by animals including humans. Leading AI "
        "textbooks define the field as the study of intelligent agents: any system that "
        "perceives its environment and takes actions that maximize its chance of achieving "
        "its goals. Some popular accounts use the term artificial intelligence to describe "
        "machines that mimic cognitive functions that humans associate with the human "
        "mind, such as learning and problem solving, however this definition is rejected "
        "by major AI researchers. AI applications include advanced web search engines, "
        "recommendation systems, understanding human speech, self-driving cars, generative "
        "or creative tools, automated decision-making and competing at the highest level "
        "in strategic game systems. As machines become increasingly capable, tasks "
        "considered to require intelligence are often removed from the definition of AI, "
        "a phenomenon known as the AI effect. For instance, optical character recognition "
        "is frequently excluded from things considered to be AI, having become a routine "
        "technology. The various sub-fields of AI research are centered around particular "
        "goals and the use of particular tools. The traditional goals of AI research "
        "include reasoning, knowledge representation, planning, learning, natural language "
        "processing, perception, and the ability to move and manipulate objects. General "
        "intelligence is among the field's long-term goals. To solve these problems, AI "
        "researchers have adapted and integrated a wide range of problem-solving techniques."
    )
    
    print(f"Testing {tool_name} with ~250 words text.")
    
    before_stats = get_tool_stats(tool_name)
    result = agent.run_tool(tool_name, {"text": text, "max_sentences": 3, "mode": "llm"})
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
