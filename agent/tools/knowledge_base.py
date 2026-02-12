import os
import json
import psycopg2
from typing import Optional, List, Dict
from langchain_openai import OpenAIEmbeddings

class KnowledgeBaseTool:
    """Tool for retrieving knowledge from the centralized vector database."""

    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.api_key = os.getenv("OPENAI_API_KEY")
        
        if not self.db_url or not self.api_key:
            # We defer raising error to method call to not break init if keys missing
            self.embeddings_model = None
        else:
            self.embeddings_model = OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=self.api_key
            )

    def search(self, query: str, app_filter: Optional[str] = None, limit: int = 5) -> str:
        """
        Search the knowledge base for relevant context.
        
        Args:
            query: The search query (e.g. "What was the RL agent performance on AAPL?")
            app_filter: Optional filter by source application ('finespresso', 'lohasulu', 'rl-agents')
            limit: Maximum number of results to return
        """
        if not self.embeddings_model:
            return json.dumps({"error": "Knowledge Base not configured (Missing DATABASE_URL or OPENAI_API_KEY)"})

        try:
            # 1. Generate Query Embedding
            query_vector = self.embeddings_model.embed_query(query)
            
            # 2. Connect to DB
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            # 3. Build SQL Query (Hybrid Search)
            sql = """
                SELECT content, source_app, source_schema, metadata, 
                       (embedding <=> %s::vector) as distance
                FROM vectors.embeddings
            """
            params = [query_vector]
            
            if app_filter:
                sql += " WHERE source_app = %s"
                params.append(app_filter)
                
            sql += " ORDER BY distance ASC LIMIT %s;"
            params.append(limit)
            
            # 4. Execute
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            
            results = []
            for row in rows:
                content, app, schema, meta, dist = row
                results.append({
                    "content": content,
                    "source": f"{app}/{schema}",
                    "similarity": round(1 - float(dist), 4), # Convert distance to similarity
                    "metadata": meta
                })
            
            cur.close()
            conn.close()
            
            if not results:
                return json.dumps({"message": "No relevant information found."})
                
            return json.dumps(results, indent=2)

        except Exception as e:
            return json.dumps({"error": f"Search failed: {str(e)}"})

    def close(self):
        pass
