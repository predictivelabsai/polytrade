import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    print(f"Checking schema: alpha_agents")
    
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'alpha_agents' 
        AND table_type='BASE TABLE'
        ORDER BY table_name;
    """)
    
    rows = cur.fetchall()
    
    if not rows:
        print("No tables found in alpha_agents.")
    else:
        print("Found tables in alpha_agents:")
        for table in rows:
            print(f"- {table[0]}")
            # quick check cols
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = 'alpha_agents' AND table_name = '{table[0]}'")
            cols = [c[0] for c in cur.fetchall()]
            print(f"  Columns: {cols}")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Error: {e}")
