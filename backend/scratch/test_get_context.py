import os
import sys

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import get_context_infrastructure

def test():
    print("Testing get_context_infrastructure with DB-first approach...")
    # Sri Rampai coordinates from existing tests
    lat = 3.1988
    lon = 101.7375
    
    entities = get_context_infrastructure(lat, lon)
    print(f"\nTotal entities returned: {len(entities)}")
    
    # Print a few samples if available
    for e in entities[:5]:
        print(f" - {e['id']}: {e['name']} ({e['entity_type']})")

if __name__ == "__main__":
    test()
