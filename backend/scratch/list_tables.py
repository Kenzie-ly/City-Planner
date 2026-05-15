import os
import sys

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.database import engine
from sqlalchemy import text

def list_tables():
    print("Listing all tables in database...")
    with engine.connect() as conn:
        try:
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)).fetchall()
            print(f"Tables: {[r[0] for r in result]}")
        except Exception as e:
            print(f"Error listing tables: {e}")

if __name__ == "__main__":
    list_tables()
