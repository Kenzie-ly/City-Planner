import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load from .env
load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    print("Error: DATABASE_URL not found in .env")
    exit(1)

engine = create_engine(DB_URL)

try:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT id, name, type, region FROM city_areas LIMIT 20"))
        rows = result.fetchall()
        
        if not rows:
            print("No data found in city_areas table.")
        else:
            print(f"{'ID':<5} | {'Name':<30} | {'Type':<15} | {'Region':<15}")
            print("-" * 75)
            for row in rows:
                print(f"{row[0]:<5} | {row[1]:<30} | {row[2]:<15} | {row[3]:<15}")
except Exception as e:
    print(f"Database error: {e}")
