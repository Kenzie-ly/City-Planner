import os
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

load_dotenv()

def list_tables():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_user = os.getenv("DB_USER", "postgres")
        db_pass = os.getenv("DB_PASS", "your-password")
        db_name = os.getenv("DB_NAME", "city_planning")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_url = f"postgresql://{db_user}:{db_pass}@{db_host}/{db_name}"
    
    try:
        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"Tables in database: {tables}")
        
        for table in tables:
            columns = inspector.get_columns(table)
            print(f"\nTable: {table}")
            for column in columns:
                print(f"  - {column['name']} ({column['type']})")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_tables()
