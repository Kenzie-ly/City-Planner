import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)

try:
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents="Hello, say 'API IS WORKING' if you can see this."
    )
    print(f"RESPONSE: {response.text}")
except Exception as e:
    print(f"ERROR: {e}")
