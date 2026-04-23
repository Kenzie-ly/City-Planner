import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
model_name = os.getenv("PLANNER_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
client = genai.Client(api_key=api_key)

try:
    response = client.models.generate_content(
        model=model_name,
        contents="Hello, say 'API IS WORKING' if you can see this."
    )
    print(f"RESPONSE: {response.text}")
except Exception as e:
    print(f"ERROR: {e}")
