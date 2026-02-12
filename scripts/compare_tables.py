import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def get_stats(table_name, date_col):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table_name};")
        count = cur.fetchone()[0]
        
        cur.execute(f"SELECT MAX({date_col}) FROM {table_name};")
        latest_date = cur.fetchone()[0]
        
        print(f"--- {table_name} ---")
        print(f"Row Count: {count}")
        print(f"Latest Date: {latest_date}")
        
    except Exception as e:
        print(f"Error checking {table_name}: {e}")
    finally:
        cur.close()
        conn.close()

def get_sample(table_name, col_name="content"):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {col_name} FROM {table_name} LIMIT 1;")
        row = cur.fetchone()
        if row:
            print(f"\n--- Sample {table_name} ---")
            print(row[0][:500] + "..." if row[0] else "None")
        else:
            print(f"\n--- Sample {table_name} ---")
            print("No rows found.")
    except Exception as e:
        print(f"Error fetching sample from {table_name}: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    print("Checking public.news columns...")
    col_str = "id, title, reason, predicted_side, industry"
    get_sample("public.news", col_str)
