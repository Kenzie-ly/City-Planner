import requests
import json
import time
import subprocess
import os
import signal

BASE_URL = "http://127.0.0.1:8000"

def wait_for_server():
    print("Waiting for server to start...")
    for _ in range(15):
        try:
            requests.get(BASE_URL)
            return True
        except:
            time.sleep(2)
    return False

def test_flow():
    # 1. Start session
    print("\n--- [1] Starting Session ---")
    try:
        res = requests.post(f"{BASE_URL}/api/start", json={}, timeout=30)
        res.raise_for_status()
        data = res.json()
        session_id = data["session_id"]
        print(f"Session ID: {session_id}")
        print(f"Bot: {data['reply']}")
    except Exception as e:
        print(f"Error starting session: {e}")
        return

    # 2. Intake City
    print("\n--- [2] Sending City: Shah Alam ---")
    try:
        res = requests.post(f"{BASE_URL}/api/chat", json={
            "session_id": session_id,
            "message": "Shah Alam"
        }, timeout=60)
        res.raise_for_status()
        data = res.json()
        print(f"Stage: {data.get('stage')}")
        print(f"Bot: {data.get('reply')[:200]}...") # Print first 200 chars
        
        if "area_options" in data:
            print(f"SUCCESS: Found {len(data['area_options'])} Area Options (Problem Direction Cards)")
        else:
            print("WARNING: No area_options in response. Check if retrieval/pack-building failed.")
    except Exception as e:
        print(f"Error in chat step 2: {e}")
        return

    # 3. Select Card 1 (Simulated)
    print("\n--- [3] Selecting Challenge Card #1 ---")
    try:
        res = requests.post(f"{BASE_URL}/api/chat", json={
            "session_id": session_id,
            "message": "1"
        }, timeout=60)
        res.raise_for_status()
        data = res.json()
        print(f"Stage: {data.get('stage')}")
        print(f"Bot: {data.get('reply')[:200]}...")
    except Exception as e:
        print(f"Error in chat step 3: {e}")

if __name__ == "__main__":
    print("Starting FastAPI server...")
    env = os.environ.copy()
    # Set PYTHONPATH to the parent directory (d:\hackathon) so 'from backend...' works
    root_dir = os.path.abspath(os.path.join(os.getcwd(), ".."))
    env["PYTHONPATH"] = root_dir
    # Use python -m uvicorn to ensure it's in the right environment
    server_process = subprocess.Popen(
        ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"],
        env=env
    )

    try:
        if wait_for_server():
            test_flow()
        else:
            print("Server failed to start in time.")
            stdout, stderr = server_process.communicate(timeout=1)
            print(f"Server STDOUT: {stdout.decode()}")
            print(f"Server STDERR: {stderr.decode()}")
    finally:
        print("\nStopping server...")
        server_process.terminate()
        server_process.wait()
