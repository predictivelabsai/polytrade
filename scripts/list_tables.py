import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    schemas = ['public', 'alpha_agents', 'rl_agents']
    print(f"Checking schemas: {schemas}")
    
    cur.execute("""
        SELECT table_schema, table_name 
        FROM information_schema.tables 
        WHERE table_schema = ANY(%s) 
        AND table_type='BASE TABLE'
        ORDER BY table_schema, table_name;
    """, (schemas,))
    
    rows = cur.fetchall()
    
    if not rows:
        print("No tables found in these schemas.")
    else:
        print("Found tables:")
        for schema, table in rows:
            print(f"- {schema}.{table}")
            
            # fast check columns
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{table}'")
            cols = [c[0] for c in cur.fetchall()]
            print(f"  Columns: {cols}")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Error: {e}")
