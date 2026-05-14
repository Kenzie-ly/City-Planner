import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables from parent folder
load_dotenv("../.env")
load_dotenv()

db_url = os.getenv("DATABASE_URL")
print(f"Connecting to database...")

try:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        # Try to enable the vector extension
        print("Attempting to enable 'vector' extension...")
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(text("COMMIT;"))
        print("✅ SUCCESS: 'pgvector' extension is enabled and ready to use!")
        
        # Check installed extensions
        result = conn.execute(text("SELECT extname FROM pg_extension;")).fetchall()
        extensions = [r[0] for r in result]
        print(f"Installed extensions: {extensions}")
        
except Exception as e:
    print(f"❌ FAILED: Could not enable pgvector.")
    print(f"Error details: {e}")
