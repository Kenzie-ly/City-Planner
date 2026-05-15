"""
STARTUP DIAGNOSTIC SCRIPT
Run this in Cloud Run (or locally) to pinpoint exactly which import or env var
is causing the container crash. Replace your CMD temporarily with:
    CMD ["python", "diagnose_startup.py"]
"""
import sys
import os

print("=== STARTUP DIAGNOSTIC ===", flush=True)
print(f"Python: {sys.version}", flush=True)
print(f"PORT env var: {os.getenv('PORT', '(not set)')}", flush=True)
print(f"DATABASE_URL set: {'YES' if os.getenv('DATABASE_URL') else 'NO'}", flush=True)
print(f"GOOGLE_API_KEY set: {'YES' if os.getenv('GOOGLE_API_KEY') else 'NO'}", flush=True)
print(f"GEMINI_API_KEY set: {'YES' if os.getenv('GEMINI_API_KEY') else 'NO'}", flush=True)
print(f"GOOGLE_APPLICATION_CREDENTIALS set: {'YES' if os.getenv('GOOGLE_APPLICATION_CREDENTIALS') else 'NO'}", flush=True)
print("", flush=True)

steps = [
    ("fastapi", "import fastapi"),
    ("uvicorn", "import uvicorn"),
    ("sqlalchemy", "import sqlalchemy"),
    ("psycopg2", "import psycopg2"),
    ("google-adk", "from google.adk.runners import Runner"),
    ("google-adk sessions", "from google.adk.sessions import InMemorySessionService"),
    ("google-genai", "from google.genai import types"),
    ("dotenv", "from dotenv import load_dotenv"),
    ("db.database", "from db.database import engine"),
    ("agent", "from agent import place_intake_agent"),
    ("evidence_pipeline", "from evidence_pipeline import audit_osm_transit_gap"),
    ("reliability", "from reliability import build_decision_package"),
    ("persistence_service", "import persistence_service"),
    ("concurrency", "from concurrency import llm_semaphore"),
]

all_ok = True
for name, stmt in steps:
    try:
        exec(stmt)
        print(f"  [OK]   {name}", flush=True)
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}", flush=True)
        all_ok = False

print("", flush=True)
if all_ok:
    print("[RESULT] All imports OK. Startup crash is NOT an import issue.", flush=True)
    print("         Check for logic errors running at module level.", flush=True)
else:
    print("[RESULT] One or more imports FAILED. Fix the above errors.", flush=True)

sys.exit(0 if all_ok else 1)
