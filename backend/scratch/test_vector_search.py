import os
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
    response = client.models.embed_content(
        model='gemini-embedding-2',
        contents=text_to_embed,
    )
    return response.embeddings[0].values

def semantic_search(city, query_text):
    print(f"\n🔍 Searching for: '{query_text}' in {city}...")
    
    # 1. Generate embedding for the search query
    query_vector = get_embedding(query_text)
    
    # Convert the list of floats to a string format pgvector understands: '[0.1, 0.2, ...]'
    vector_str = "[" + ",".join(map(str, query_vector)) + "]"
    
    # 2. Query the database using Cosine Distance (<=>)
    # Smaller distance means more similar!
    query = """
        SELECT title, content, embedding <=> :vector AS distance
        FROM city_problems_rag
        WHERE city = :city
        ORDER BY distance ASC
        LIMIT 3;
    """
    
    with engine.connect() as conn:
        result = conn.execute(text(query), {"vector": vector_str, "city": city}).fetchall()
        
        print(f"Found {len(result)} results:")
        for r in result:
            similarity = round(1 - r.distance, 4) # Convert distance to similarity score
            print(f"\n📌 Title: {r.title}")
            print(f"📊 Similarity Score: {similarity}")
            print(f"📝 Content: {r.content[:150]}...")

if __name__ == "__main__":
    semantic_search("cyberjaya", "smart traffic lights and road repairs")

