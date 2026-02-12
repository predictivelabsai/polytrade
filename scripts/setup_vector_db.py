import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    print("Error: DATABASE_URL not found in environment variables.")
    exit(1)

def setup_vector_db():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        print("Connected to database.")

        # 1. Check/Enable pgvector extension
        print("Checking for 'vector' extension...")
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
        if cur.fetchone():
            print("Extension 'vector' is already installed.")
        else:
            print("Extension 'vector' not found. Attempting to create...")
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.commit() # Commit immediately to save state
                print("Extension 'vector' created successfully.")
            except psycopg2.errors.InsufficientPrivilege:
                conn.rollback()
                print("Error: Permission denied creating extension 'vector'.")
                print("HINT: The extension is missing on THIS specific database ('finespresso_db').")
                print("Please run this on your server:")
                print("   sudo -u postgres psql -d finespresso_db -c \"CREATE EXTENSION vector;\"")
                exit(1)
            except Exception as e:
                conn.rollback()
                print(f"Error creating extension: {e}")
                exit(1)
        
        # 2. Create vectors schema (idempotent)
        print("Creating 'vectors' schema...")
        cur.execute("CREATE SCHEMA IF NOT EXISTS vectors;")

        # 3. Create embeddings table (idempotent)
        print("Creating 'embeddings' table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vectors.embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                content TEXT NOT NULL,
                embedding vector(1536),
                source_app VARCHAR(50) NOT NULL,
                source_schema VARCHAR(50) NOT NULL,
                metadata JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # 4. Create indexes (idempotent-ish, best effort)
        print("Creating indexes...")
        # Check if index exists before creating to avoid errors or redundancy
        # HNSW Index for cosine similarity
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = 'embeddings_embedding_idx' AND n.nspname = 'vectors'
                ) THEN
                    CREATE INDEX embeddings_embedding_idx ON vectors.embeddings USING hnsw (embedding vector_cosine_ops);
                END IF;
            END$$;
        """)

        # GIN Index for metadata filtering
        cur.execute("""
             DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = 'embeddings_metadata_idx' AND n.nspname = 'vectors'
                ) THEN
                    CREATE INDEX embeddings_metadata_idx ON vectors.embeddings USING gin (metadata);
                END IF;
            END$$;
        """)

        conn.commit()
        print("Vector database setup completed successfully!")
        print("CREATED: Schema 'vectors', Table 'vectors.embeddings'")

        cur.close()
        conn.close()

    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    setup_vector_db()
