import sys
import os
sys.path.append(os.getcwd())

from db.database import engine, MIGRATION_QUERIES
from sqlalchemy import text

def run_migrations():
    print("Applying migrations...")
    with engine.connect() as conn:
        for query in MIGRATION_QUERIES:
            print(f"Executing: {query}")
            conn.execute(text(query))
        conn.execute(text("COMMIT"))
    print("Migrations applied successfully.")

if __name__ == "__main__":
    run_migrations()
