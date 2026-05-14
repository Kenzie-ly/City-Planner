import os
from dotenv import load_dotenv
from google import genai

# Load environment variables
load_dotenv("../.env")
load_dotenv()

# Initialize Gemini Client
client = genai.Client()

print("Fetching available models from Google...")
try:
    # List all models
    models = client.models.list()
    
    print("\nAvailable Models:")
    for m in models:
        print(f"👉 {m.name}")
            
except Exception as e:
    print(f"Error listing models: {e}")
