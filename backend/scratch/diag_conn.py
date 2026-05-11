import socket
import os

target_host = "34.126.129.235"
target_port = 5432

def check_port():
    print(f"Checking connectivity to {target_host}:{target_port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        result = sock.connect_ex((target_host, target_port))
        if result == 0:
            print("  SUCCESS: Port is OPEN and reachable.")
        else:
            print(f"  FAILURE: Port is CLOSED or BLOCKED (Error code: {result}).")
        sock.close()
    except Exception as e:
        print(f"  ERROR: {e}")

if __name__ == "__main__":
    check_port()
