from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import sys
from pathlib import Path
import asyncio

# Add project root to sys.path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.tools.polymarket_tool import PolymarketClient
from agent.tools.visual_crossing_client import VisualCrossingClient
from utils.backtest_engine import BacktestEngine

app = FastAPI(title="PolyCode API", description="API for PolyCode Polymarket Research Agent")

# Models
class ForecastRequest(BaseModel):
    city: str
    days: int = 7

class PredictRequest(BaseModel):
    city: str
    days: int = 7
    lookback_days: int = 7

# Dependencies
# We'll instantiate clients per request or use a dependency injection system in a real app.
# For simplicity, we'll instantiate locally.

@app.get("/")
async def root():
    return {"message": "PolyCode API is running", "status": "active"}

@app.post("/weather")
async def get_weather(request: ForecastRequest):
    """Get weather forecast for a city."""
    vc_client = VisualCrossingClient()
    try:
        # VisualCrossingClient.get_forecast returns a dict or raises
        forecast = await vc_client.get_forecast(request.city)
        
        # Filter for requested days if needed, but client usually returns default.
        # Minimal processing for now.
        return {"city": request.city, "forecast": forecast}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await vc_client.close()

@app.post("/predict")
async def run_prediction(request: PredictRequest):
    """Run market prediction analysis."""
    pm_client = PolymarketClient()
    vc_client = VisualCrossingClient()
    engine = BacktestEngine(pm_client, vc_client)
    
    try:
        # Using today's date implicitly via BacktestEngine logic if date is None?
        # engine.run_backtest signature: city, date_str (YYYY-MM-DD), days, is_prediction
        # If running purely for prediction, we usually want "future" logic which BacktestEngine now supports via is_prediction=True
        
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        result = await engine.run_backtest(
            city=request.city,
            end_date_str=today_str, # In prediction mode, this might be start date? 
                                    # Looking at CLI: _run_backtest_handler passes date=today for prediction?
                                    # Actually CLI calls: run_backtest(city, date, lookback_days, is_prediction=True)
                                    # Let's double check implementation of CLI in a bit.
            days_to_test=request.days,     # This is 'lookback' in CLI args but 'days' in engine?
                                    # In CLI "poly:predict London 2" -> days=2.
                                    # calling engine.run_backtest(city, today, lookback_days=days, ...)
            is_prediction=True
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await pm_client.close()
        await vc_client.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
