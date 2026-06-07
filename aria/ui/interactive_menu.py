"""
aria/ui/interactive_menu.py
Interactive CLI menu using Questionary with a strictly monochromatic theme.
"""

import sys
import re
import datetime
import questionary
from questionary import Style

from aria.core.agent import agent
from aria.config import settings

try:
    from groq import Groq
except ImportError:
    Groq = None

# Monochromatic white-on-black style
bw_style = Style([
    ('qmark', 'fg:white'),
    ('question', 'fg:white'),
    ('answer', 'fg:white'),
    ('pointer', 'fg:white bold'),
    ('highlighted', 'fg:white bold'),
    ('selected', 'fg:white'),
    ('separator', 'fg:white'),
    ('instruction', 'fg:white'),
    ('text', 'fg:white'),
])

def get_confidence_score(tool_name: str, user_input: str, result_str: str) -> str:
    """Uses Groq to generate a confidence score."""
    if not Groq:
        return "N/A"
    try:
        client = Groq(api_key=settings.groq_api_key)
        prompt = (
            f"Tool used: {tool_name}\n"
            f"User Input: {user_input}\n"
            f"Output from tool: {result_str}\n\n"
            "Grade this output brutally on a scale of 1 to 10. Start at 10 and deduct 2 points for any minor formatting issues, "
            "3 points for lack of detail, and 5 points if it doesn't fully answer the prompt or handle edge cases. "
            "Return ONLY the final single integer (e.g., '6'). Do NOT show your math or reasoning. Output NOTHING but the final digit."
        )
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0
        )
        score_str = response.choices[0].message.content.strip()
        
        # Clean the output to guarantee a single number
        nums = re.findall(r'\d+', score_str)
        if nums:
            return nums[-1]
        return score_str
    except Exception:
        return "N/A"

def save_output(tool_name: str, output_text: str, score: str):
    """Prompt the user to save the output."""
    save = questionary.select(
        "Save output?",
        choices=["Yes", "No"],
        style=bw_style
    ).ask()
    
    if save == "Yes":
        import os
        os.makedirs("Results", exist_ok=True)
        
        now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        now_gmt = datetime.datetime.now(datetime.timezone.utc)
        
        ist_str = now_ist.strftime("%d/%m/%Y [%H:%M:%S IST]")
        gmt_str = now_gmt.strftime("%d/%m/%Y [%H:%M:%S GMT]")
        
        file_name = f"output_{tool_name}.md"
        file_path = os.path.join("Results", file_name)
        
        content = "Date and time of output:\n"
        content += f"{ist_str}\n"
        content += f"{gmt_str}\n"
        content += f"Confidence score: {score}\n"
        content += " A partition with a long line :\n"
        content += "----------------------------------------------------------------------\n"
        content += "Then the output:\n"
        content += output_text + "\n\n"
        
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content)
        print(f"Saved to {file_path}")

def handle_tool(tool_name: str, prompt_msg: str, input_key: str):
    user_input = questionary.text(prompt_msg, style=bw_style).ask()
    if not user_input:
        return
        
    print(f"\nRunning {tool_name}...\n")
    
    input_data = {input_key: user_input}
    if tool_name == "summarizer_tool":
        input_data["mode"] = "llm"
        input_data["max_sentences"] = 3
        
    result = agent.run_tool(tool_name, input_data)
    
    if not result:
        print("Error: Tool not found in registry.\n")
        return
        
    if result.success:
        res_str = str(result.output)
        # Format Search Tool Results
        if tool_name == "search_tool" and isinstance(result.output, list):
            formatted_res = ""
            for item in result.output:
                formatted_res += f"Title: {item.get('title')}\nURL: {item.get('url')}\nSnippet: {item.get('snippet')}\n\n"
            res_str = formatted_res.strip()
        # Format Weather Tool
        elif tool_name == "weather_tool" and isinstance(result.output, dict):
            w = result.output
            res_str = f"City: {w.get('city')}, {w.get('country')}\nTemp: {w.get('temperature')}\nCondition: {w.get('condition')}\nWind: {w.get('wind_speed_kmh')} km/h\nHumidity: {w.get('humidity_percent')}%"
            
        print(f"{res_str}\n")
        
        print("Calculating confidence score...")
        score_str = get_confidence_score(tool_name, user_input, res_str)
        print(f"Confidence Score: {score_str}/10\n")
        
        save_output(tool_name, res_str, score_str)
        
        try:
            score_val = float(score_str)
            if score_val < 9:
                print(f"\n[!] Confidence score is {score_val} (less than 9). Triggering autonomous improvement loop for {tool_name}...\n")
                agent.run_improvement_cycle(target_tool=tool_name)
        except ValueError:
            pass
    else:
        print(f"Tool execution failed: {result.error}\n")

def run_menu():
    """Main interactive menu loop."""
    while True:
        choice = questionary.select(
            "ARIA Interactive Terminal",
            choices=[
                "Calculator Tool",
                "Weather Tool",
                "Summarizer Tool",
                "Search Tool",
                "Code Execution Tool",
                "Exit"
            ],
            style=bw_style
        ).ask()
        
        if choice == "Calculator Tool":
            handle_tool("calculator_tool", "Enter expression: ", "expression")
        elif choice == "Weather Tool":
            handle_tool("weather_tool", "Enter city name: ", "city")
        elif choice == "Summarizer Tool":
            handle_tool("summarizer_tool", "Enter topic/text to summarize: ", "text")
        elif choice == "Search Tool":
            handle_tool("search_tool", "Enter search topic: ", "query")
        elif choice == "Code Execution Tool":
            handle_tool("code_executor_tool", "Enter coding topic/problem: ", "topic")
        elif choice == "Exit" or choice is None:
            print("Exiting ARIA...")
            sys.exit(0)
