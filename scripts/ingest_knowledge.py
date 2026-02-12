import os
import time
import json
import psycopg2
from typing import List, Dict, Any
from dotenv import load_dotenv

# LangChain Imports
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DB_URL or not OPENAI_API_KEY:
    print("Error: DATABASE_URL or OPENAI_API_KEY missing.")
    exit(1)

# Initialize Embedding Model
embeddings_model = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=OPENAI_API_KEY
)

# Initialize Splitter
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
)

def get_db_connection():
    return psycopg2.connect(DB_URL)

def fetch_data(query: str, id_column_index: int = 0) -> List[Dict]:
    """Fetch rows using a raw SQL query. 
    Assumes the first column (index 0) is the ID unless specified otherwise.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        print(f"Executing Query: {query.strip().splitlines()[0]}...")
        cur.execute(query)
        rows = cur.fetchall()
        
        results = []
        if not rows:
            return []
            
        columns = [desc[0] for desc in cur.description]
        
        for r in rows:
            row_dict = dict(zip(columns, r))
            row_id = str(r[id_column_index])
            
            # Construct Rich Text Content dynamically based on available columns
            content_parts = []
            
            # --- 1. News & Predictions ---
            if "title" in row_dict and row_dict["title"]:
                content_parts.append(f"Title: {row_dict['title']}")
            if "company" in row_dict and row_dict["company"]:
                content_parts.append(f"Company: {row_dict['company']}")
            if "event_standardized" in row_dict and row_dict["event_standardized"]:
                content_parts.append(f"Standardized Event: {row_dict['event_standardized']}")
            elif "event" in row_dict and row_dict["event"]:
                content_parts.append(f"Event: {row_dict['event']}")
            
            if "predicted_side" in row_dict and row_dict["predicted_side"]:
                move = row_dict.get('predicted_move', 'N/A')
                content_parts.append(f"Model Prediction: {row_dict['predicted_side']} (Expected Move: {move})")
            
            if "actual_side" in row_dict and row_dict["actual_side"]:
                pct = row_dict.get('price_change_percentage')
                pct_str = f" ({pct}%)" if pct is not None else ""
                content_parts.append(f"Actual Outcome: {row_dict['actual_side']}{pct_str}")

            # --- 2. Model Tracking ---
            if "model_id" in row_dict and "accuracy" in row_dict:
                content_parts.append(f"## Model Performance: {row_dict['model_id']}")
                content_parts.append(f"Type: {row_dict.get('model_type')} | Category: {row_dict.get('model_category')}")
                content_parts.append(f"Metrics: Accuracy={row_dict.get('accuracy')}, F1={row_dict.get('f1_score')}, precision={row_dict.get('precision')}, recall={row_dict.get('recall')}")
                if "feature_columns" in row_dict:
                    content_parts.append(f"Features Used: {row_dict['feature_columns']}")
                if "best_params" in row_dict:
                    content_parts.append(f"Best Parameters: {row_dict['best_params']}")

            # --- 3. Backtest & Trades ---
            if "model_name" in row_dict and "total_pnl" in row_dict:
                content_parts.append(f"## Backtest Summary: {row_dict['model_name']}")
                content_parts.append(f"Period: {row_dict.get('start_date')} to {row_dict.get('end_date')}")
                content_parts.append(f"P&L: {row_dict.get('total_pnl')} ({row_dict.get('return_percent')}%)")
                content_parts.append(f"Stats: Win Rate={row_dict.get('win_rate_percent')}%, Sharpe={row_dict.get('sharpe_ratio')}, Max Drawdown={row_dict.get('max_drawdown')}")
            
            if "ticker" in row_dict and "pnl" in row_dict:
                is_paper = " (PAPER)" if row_dict.get("is_paper") else ""
                content_parts.append(f"## Trade{is_paper}: {row_dict['ticker']} ({row_dict.get('direction') or row_dict.get('side')})")
                content_parts.append(f"Entry: {row_dict.get('entry_price')} | Exit: {row_dict.get('exit_price')} | P&L: {row_dict.get('pnl')} ({row_dict.get('pnl_pct')}%)")
                if "reason" in row_dict and row_dict["reason"]:
                    content_parts.append(f"Reason: {row_dict['reason']}")
                if "news_event" in row_dict and row_dict["news_event"]:
                    content_parts.append(f"Linked Event: {row_dict['news_event']}")

            # --- 4. Qualitative Analysis (Lohasulu) ---
            if "moat_brand_monopoly_justification" in row_dict:
                content_parts.append(f"## Qualitative Analysis: {row_dict.get('company_name')}")
                for k, v in row_dict.items():
                    if "justification" in k and v:
                        label = k.replace("_", " ").title()
                        content_parts.append(f"### {label}\n{v}")

            # --- 5. Generic Fallbacks ---
            if "reasoning" in row_dict and row_dict["reasoning"]:
                content_parts.append(f"## Reasoning\n{row_dict['reasoning']}")
            
            # Main Body Content fallback
            body = row_dict.get('content') or row_dict.get('news_content')
            if body and len(content_parts) < 3: # Only add full body if we don't have much else
                content_parts.append(f"## Content\n{body}")

            full_text = "\n\n".join(content_parts)
            
            if full_text.strip():
                results.append({"id": row_id, "text": full_text, "row_data": row_dict})
                
        return results
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def ingest_documents(documents: List[Dict]):
    """Process, chunk, embed, and store documents."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    total_chunks = 0
    
    for doc in documents:
        raw_text = doc["text"] or ""
        cleaned_text = raw_text.replace('\x00', '').strip()
        
        if not cleaned_text:
            continue

        try:
            source_label = doc.get("source", "unknown")
            chunks = text_splitter.create_documents([cleaned_text], metadatas=[{"source_id": doc["id"], "source": source_label}])
            
            for chunk in chunks:
                vector = embeddings_model.embed_query(chunk.page_content)
                
                # Determine App Name from Source Label
                app_name = "finespresso"
                if "alpha" in source_label or "lohasulu" in source_label: app_name = "lohasulu" 
                elif "rl" in source_label: app_name = "rl-agents"
                
                schema_name = source_label.split(".")[0] if "." in source_label else "public"

                cur.execute("""
                    INSERT INTO vectors.embeddings (content, embedding, source_app, source_schema, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    chunk.page_content,
                    vector,
                    app_name,
                    schema_name,
                    json.dumps(chunk.metadata)
                ))
                total_chunks += 1
                
        except Exception as e:
            print(f"Skipping record {doc.get('id', 'unknown')} due to error: {e}")
            continue
            
    conn.commit()
    print(f"Successfully ingested {total_chunks} chunks.")
    cur.close()
    conn.close()

def main():
    print("Starting Centralized Ingestion Pipeline...")
    
    # 1. Enriched News & Training Data
    enriched_news_query = """
        SELECT 
            n.id, n.title, n.content, n.published_date, n.company, n.reason, 
            n.industry, n.event, n.publisher, n.status, n.yf_ticker, 
            n.predicted_side, n.predicted_move,
            t.market_status, t.actual_side, t.nextday_side, 
            t.price_change, t.price_change_percentage, t.event_standardized
        FROM public.news n
        LEFT JOIN public.training_ml_data t ON n.id = t.news_id
        WHERE n.content IS NOT NULL
        ORDER BY n.published_date DESC;
    """
    
    # 2. Model Tracking (Performance)
    model_tracking_query = """
        SELECT id, model_id, model_type, model_category, accuracy, f1_score, 
               precision, recall, feature_columns, best_params, cv_folds
        FROM public.model_tracking
        ORDER BY created_at DESC;
    """
    
    # 3. Backtest Summary
    backtest_summary_query = """
        SELECT id, model_name, start_date, end_date, total_pnl, return_percent, 
               win_rate_percent, sharpe_ratio, max_drawdown, agent
        FROM public.backtest_summary
        ORDER BY timestamp DESC;
    """
    
    # 4. Backtest & Individual Trades
    trades_query = """
        SELECT id, ticker, direction as side, entry_price, exit_price, pnl, pnl_pct, 
               news_event, news_id, agent, 'backtest' as trade_type, false as is_paper
        FROM public.backtest_trades
        UNION ALL
        SELECT id, symbol as ticker, side, entry_price, exit_price, pnl, pnl_pct, 
               news_event, news_id, agent, 'live_or_paper' as trade_type, is_paper
        FROM public.individual_trades;
    """
    
    # 5. RL Agents Trades
    rl_trades_query = """
        SELECT id, symbol as ticker, side, entry_price, exit_price, pnl, pnl_pct, 
               reason, agent_name as agent, strategy_name
        FROM rl_agents.trades;
    """
    
    # 6. Lohasulu (Qualitative)
    lohasulu_query = """
        SELECT id, company_name, moat_brand_monopoly_justification, moat_barriers_to_entry_justification, 
               management_quality_justification, competitive_differentiation_justification,
               major_risk_factors_justification
        FROM public.qualitative_analysis 
        ORDER BY analysis_timestamp DESC;
    """
    
    tasks = [
        ("public.enriched_news", enriched_news_query),
        ("public.model_tracking", model_tracking_query),
        ("public.backtest_summary", backtest_summary_query),
        ("public.trades_all", trades_query),
        ("rl_agents.trades", rl_trades_query),
        ("public.qualitative_analysis", lohasulu_query)
    ]
    
    for source_name, query in tasks:
        print(f"--- Processing {source_name} ---")
        docs = fetch_data(query)
        if docs:
            for d in docs: d["source"] = source_name
            print(f"Found {len(docs)} records. Ingesting...")
            ingest_documents(docs)
        else:
            print(f"No records found for {source_name}")

if __name__ == "__main__":
    main()
