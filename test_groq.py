import os
from dotenv import load_dotenv
import groq

load_dotenv()
print("GROQ_API_KEY:", os.getenv("GROQ_API_KEY")[:5] + "..." if os.getenv("GROQ_API_KEY") else "None")

try:
    client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=10
    )
    print("Success:", response.choices[0].message.content)
except Exception as e:
    print("Error type:", type(e))
    print("Error str:", str(e))
    import traceback
    traceback.print_exc()
