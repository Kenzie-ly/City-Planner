import requests
import json

BASE_URL = "http://127.0.0.1:8001"

def test_workflow():
    print("Starting session...")
    start_resp = requests.post(f"{BASE_URL}/api/start", json={})
    print(f"START STATUS: {start_resp.status_code}")
    if start_resp.status_code != 200:
        print(f"ERROR: {start_resp.text}")
        return
    
    start_data = start_resp.json()
    session_id = start_data["session_id"]
    print(f"SESSION ID: {session_id}")
    print(f"GREETING: {start_data['reply']}")

    print("\nSending 'kuala lumpur'...")
    chat_resp = requests.post(f"{BASE_URL}/api/chat", json={
        "session_id": session_id,
        "message": "kuala lumpur"
    })
    print(f"CHAT STATUS: {chat_resp.status_code}")
    if chat_resp.status_code != 200:
        print(f"ERROR: {chat_resp.text}")
        return
    
    chat_data = chat_resp.json()
    print(f"REPLY: {chat_data['reply']}")

if __name__ == "__main__":
    test_workflow()
