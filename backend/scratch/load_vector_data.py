import os
import json
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from google import genai

# Load environment variables
load_dotenv("../.env")
load_dotenv()

db_url = os.getenv("DATABASE_URL")
engine = create_engine(db_url)

# Initialize Gemini Client
client = genai.Client()

def get_embedding(text_to_embed):
    """Generates a 768-dimension embedding using Gemini"""
    try:
        response = client.models.embed_content(
            model='gemini-embedding-2',
            contents=text_to_embed,
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def setup_database():
    """Creates the table with the vector column"""
    with engine.connect() as conn:
        print("Dropping old table if exists to update schema...")
        conn.execute(text("DROP TABLE IF EXISTS city_problems_rag;"))
        
        print("Creating table 'city_problems_rag' with source_type...")
        conn.execute(text("""
            CREATE TABLE city_problems_rag (
                id SERIAL PRIMARY KEY,
                city VARCHAR(50),
                source_type VARCHAR(20),  -- 'government', 'public', 'news'
                title TEXT,
                content TEXT,
                embedding vector(3072)
            );
        """))
        conn.execute(text("COMMIT;"))
        print("✅ Table created successfully!")

def load_data_from_json(city_name, json_path):
    """Reads JSON and inserts into DB with embeddings"""
    if not os.path.exists(json_path):
        print(f"File not found: {json_path}")
        return
        
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Assume the JSON is a list of objects with 'title' and 'content'
    # or just a dict of keys. Let's adapt to a standard list format:
    articles = []
    if isinstance(data, list):
        articles = data
    elif isinstance(data, dict):
        # If it's a dict, let's treat keys as titles and values as content
        for k, v in data.items():
            articles.append({"title": k, "content": str(v)})
            
    print(f"Found {len(articles)} articles for {city_name}. Generating embeddings...")
    
    with engine.connect() as conn:
        for i, article in enumerate(articles):
            title = article.get('title', 'Untitled')
            content = article.get('snippet', '') # Use snippet!
            source_type = article.get('type', 'public') # Use type!
            
            if not content:
                print(f"⏩ Skipping {title[:20]} (No content)")
                continue
                
            print(f"[{i+1}/{len(articles)}] Embedding: {title[:30]}...")
            
            # Combine title and content for better meaning
            text_to_embed = f"{title}\n{content}"
            vector = get_embedding(text_to_embed)
            
            if vector is None:
                print(f"❌ Failed to get embedding for: {title[:20]}")
                continue
                
            # Insert into DB
            conn.execute(text("""
                INSERT INTO city_problems_rag (city, source_type, title, content, embedding)
                VALUES (:city, :source_type, :title, :content, :vector);
            """), {
                "city": city_name,
                "source_type": source_type,
                "title": title,
                "content": content,
                "vector": vector
            })
            print(f"➡️ Inserted: {title[:20]}")
            
        conn.execute(text("COMMIT;"))
        print(f"✅ Successfully loaded {len(articles)} articles for {city_name} into pgvector!")

if __name__ == "__main__":
    setup_database()
    
    import glob
    
    # Find all files ending with _data.json in knowledge_base
    data_files = glob.glob("knowledge_base/*_data.json")
    print(f"Found {len(data_files)} data files to load!")
    
    for file_path in data_files:
        # Extract city name from filename (e.g., "knowledge_base/shah_alam_data.json" -> "shah_alam")
        filename = os.path.basename(file_path)
        city_name = filename.replace("_data.json", "")
        
        print(f"\n=========================================")
        print(f"Processing city: {city_name}...")
        print(f"=========================================")
        load_data_from_json(city_name, file_path)
        
    print("\n🎉 ALL CITIES LOADED SUCCESSFULLY INTO PGVECTOR!")
