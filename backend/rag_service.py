import os
import json
import pickle
import numpy as np
from datetime import datetime
import re
from typing import List, Dict, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

class RagService:
    def __init__(self, kb_dir: str = "knowledge_base", index_file: str = "rag_index.pkl"):
        self.kb_dir = kb_dir
        self.index_file = os.path.join(kb_dir, index_file)
        self.documents: List[Dict[str, Any]] = []
        self.ingested_sources: set = set()
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self.tfidf_matrix = None
        
        if not os.path.exists(self.kb_dir):
            os.makedirs(self.kb_dir)
            
        self.load_index()

    def load_index(self):
        """Loads the vector index from disk if it exists."""
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, 'rb') as f:
                    data = pickle.load(f)
                    self.documents = data.get('documents', [])
                    self.ingested_sources = data.get('ingested_sources', set())
                    self.vectorizer = data.get('vectorizer')
                    self.tfidf_matrix = data.get('tfidf_matrix')
                print(f"[RAG] Loaded {len(self.documents)} documents from index.")
            except Exception as e:
                print(f"[RAG] Error loading index: {e}")

    def save_index(self):
        """Saves the vector index to disk."""
        try:
            with open(self.index_file, 'wb') as f:
                pickle.dump({
                    'documents': self.documents,
                    'ingested_sources': self.ingested_sources,
                    'vectorizer': self.vectorizer,
                    'tfidf_matrix': self.tfidf_matrix
                }, f)
        except Exception as e:
            print(f"[RAG] Error saving index: {e}")

    def _extract_year(self, published_at: str) -> int:
        """Helper to extract a 4-digit year from a string."""
        if not published_at:
            return 0
        match = re.search(r'\b(20\d{2})\b', str(published_at))
        return int(match.group(1)) if match else 0

    def add_documents(self, new_docs: List[Dict[str, Any]]):
        """
        Adds new documents to the knowledge base and updates the index.
        """
        if not new_docs:
            return
            
        for doc in new_docs:
            if 'metadata' in doc and doc['metadata'].get('published_at'):
                doc['year'] = self._extract_year(doc['metadata']['published_at'])
            elif 'year' not in doc:
                # If no year, assume current year if it's a new discovery, else 0
                doc['year'] = self._extract_year(str(doc.get('text', ''))) or datetime.now().year
                
            if doc.get('source'):
                self.ingested_sources.add(doc.get('source'))
                
        self.documents.extend(new_docs)
        
        # Re-build the TF-IDF matrix
        texts = [doc['text'] for doc in self.documents]
        self.tfidf_matrix = self.vectorizer.fit_transform(texts)
        self.save_index()
        print(f"[RAG] Added {len(new_docs)} docs. Total: {len(self.documents)}")

    def query(self, query_text: str, top_k: int = 6, max_age_years: int = 5, location_filter: str = None) -> List[Dict[str, Any]]:
        """
        Retrieves relevant snippets with Balanced Retrieval across different types
        (News, Reports, Complaints). If location_filter is provided, it boosts results
        matching that location.
        """
        if not self.documents or self.tfidf_matrix is None:
            return []
            
        current_year = datetime.now().year
        query_vec = self.vectorizer.transform([query_text])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        
        # Group candidates by type
        typed_results = {
            "news_discovery": [],
            "report": [],
            "complaint": [],
            "other": []
        }
        
        candidate_indices = similarities.argsort()[::-1]
        
        for idx in candidate_indices:
            if similarities[idx] <= 0:
                continue
                
            doc = self.documents[idx]
            doc_year = doc.get('year', 0)
            
            # Freshness Filter
            if doc_year > 0 and (current_year - doc_year) > max_age_years:
                continue
                
            # Load all known cities for exclusion logic (Cache this in a real app)
            all_cities = []
            try:
                with open("backend/regions.json", "r") as f:
                    regions_data = json.load(f)
                    for r_id in regions_data:
                        all_cities.extend(regions_data[r_id].get("cities", []))
            except: pass

            res = doc.copy()
            score = float(similarities[idx])
            is_location_match = False
            
            if location_filter:
                doc_text = doc.get('text', '').lower()
                user_query = location_filter.strip().lower()
                target_city = user_query # Fallback
                
                # Normalize user query (e.g. 'petalingjaya' -> 'petaling jaya')
                for official_city in all_cities:
                    if official_city.lower().replace(" ", "") == user_query.replace(" ", ""):
                        target_city = official_city.lower()
                        break
                
                # Create a space-agnostic pattern for the normalized city name
                target_pattern = re.escape(target_city).replace(r"\ ", r"\s*")
                
                # SPECIAL CASE: Petaling Jaya 'PJ' code recognition
                if "petaling" in target_city and "jaya" in target_city and re.search(r"\bPJ\d+\b", doc.get('text', '')):
                    score += 15.0 # Super boost for PJ stops
                    is_location_match = True
                
                # 1. Full Phrase Match (High Priority, Space-Agnostic)
                if re.search(rf"\b{target_pattern}\b", doc_text):
                    score += 10.0
                    is_location_match = True
                else:
                    # 2. Strict Exclusion: If it mentions a DIFFERENT known city, it's a mismatch
                    is_other_city = False
                    for other_city in all_cities:
                        oc_lower = other_city.lower()
                        if oc_lower == target_city: continue
                        if re.search(rf"\b{re.escape(oc_lower)}\b", doc_text):
                            is_other_city = True
                            break
                    
                    if not is_other_city:
                        # 3. Fallback to Word-by-Word (Requirement: Multi-word cities need at least 2 matches)
                        filter_words = [w.strip(",.?!").lower() for w in target_city.split() if len(w) > 3]
                        match_count = 0
                        for word in filter_words:
                            if re.search(rf"\b{re.escape(word)}\b", doc_text):
                                match_count += 1
                        
                        # If city has >1 word, require at least 2 matches to prevent 'Desa Petaling' trap
                        required_matches = 2 if len(filter_words) > 1 else 1
                        if match_count >= required_matches:
                            score += 2.0
                            is_location_match = True
            
            res['score'] = score
            res['is_location_match'] = is_location_match
            
            res['score'] = score
            res['is_location_match'] = is_location_match
            
            doc_type = doc.get('type', 'other').lower()
            if "news" in doc_type:
                typed_results["news_discovery"].append(res)
            elif "report" in doc_type or "planning" in doc_type:
                typed_results["report"].append(res)
            elif "complaint" in doc_type or "issue" in doc_type:
                typed_results["complaint"].append(res)
            else:
                typed_results["other"].append(res)

        # STRICTOR LOCATION FILTER (Strict City Shield)
        if location_filter:
            any_location_match = any(r.get('is_location_match') for cat in typed_results for r in typed_results[cat])
            if any_location_match:
                # If we have matches, PURGE anything that doesn't match the city
                for cat in typed_results:
                    typed_results[cat] = [r for r in typed_results[cat] if r.get('is_location_match')]
            else:
                # SHIELD: If we asked for a city but found ZERO local matches, return empty.
                # This prevents the AI from "borrowing" data from other cities.
                print(f"[RAG] No local documents found for '{location_filter}'. Returning empty to force web search.")
                return []

        # Balanced Selection Logic: Try to get an even split from each category
        final_results = []
        per_type_limit = max(1, top_k // 3)
        
        # 1. Take top N from each primary category
        for category in ["news_discovery", "report", "complaint"]:
            final_results.extend(typed_results[category][:per_type_limit])
            
        # 2. If we still need more to reach top_k, fill from 'other' or remaining pool
        remaining_pool = []
        for category in typed_results:
            # Add items we haven't already picked
            already_picked_texts = {r['text'] for r in final_results}
            remaining_pool.extend([r for r in typed_results[category] if r['text'] not in already_picked_texts])
            
        remaining_pool.sort(key=lambda x: x['score'], reverse=True)
        final_results.extend(remaining_pool[:(top_k - len(final_results))])
        
        # Final sort by score for the agent
        final_results.sort(key=lambda x: x['score'], reverse=True)
        return final_results[:top_k]

    def ingest_directory(self):
        """Ingests all .txt and .json files in the kb_dir."""
        new_docs = []
        for filename in os.listdir(self.kb_dir):
            if filename in self.ingested_sources:
                continue
                
            file_path = os.path.join(self.kb_dir, filename)
            
            # Skip the index file itself
            if filename == os.path.basename(self.index_file):
                continue
                
            if filename.endswith(".txt"):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    new_docs.append({
                        "text": content,
                        "source": filename,
                        "type": "planning_document"
                    })
            elif filename.endswith(".json"):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            text = item.get("text") or item.get("complaint") or item.get("snippet")
                            if text:
                                new_docs.append({
                                    "text": text,
                                    "source": filename,
                                    "type": item.get("type", "complaint"),
                                    "metadata": item
                                })
                    elif isinstance(data, dict):
                        text = data.get("text") or data.get("content")
                        if text:
                            new_docs.append({
                                "text": text,
                                "source": filename,
                                "type": data.get("type", "general"),
                                "metadata": data
                            })
                            
        if new_docs:
            self.add_documents(new_docs)
            # Remove processed files or move them? 
            # For now, let's just avoid re-ingesting the same files by checking sources.
            # (A better way would be to track filenames in the index).
