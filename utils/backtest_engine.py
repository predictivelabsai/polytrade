"""Backtest engine for Polymarket weather strategy."""
import logging
import os
import csv
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import json
import re

from agent.tools.polymarket_tool import PolymarketClient
from agent.tools.visual_crossing_client import VisualCrossingClient
from agent.tools.trading_strategy import TradingStrategy, TradeSignal

logger = logging.getLogger(__name__)

# Constants
ALLOCATION_PER_TRADE = 100.0

class BacktestEngine:
    """Engine for running historical simulations of the weather strategy."""

    ALLOCATION_PER_TRADE = 100.0

    def __init__(
        self,
        polymarket_client: PolymarketClient,
        weather_client: VisualCrossingClient,
        tomorrow_client: Optional[Any] = None,
        strategy_params: Optional[Dict[str, Any]] = None
    ):
        """Initialize the backtest engine."""
        self.pm_client = polymarket_client
        self.vc_client = weather_client
        self.tm_client = tomorrow_client
        self.strategy = TradingStrategy(**(strategy_params or {}))

    async def _get_weather_data(self, city: str, date_str: str) -> Optional[Dict[str, Any]]:
        """Router for weather data. VC is now always primary for calculations."""
        # Visual Crossing is the primary source for all backtests and predictions
        return await self.vc_client.get_day_weather(city, date_str)

    async def run_backtest(
        self,
        city: str,
        target_date: str,
        lookback_days: int = 7,
        is_prediction: bool = False,
        v2_mode: bool = False
    ) -> Dict[str, Any]:
        """Run a cross-sectional backtest for a specific city and date range."""
        total_invested = 0
        total_payout = 0
        resolved_invested = 0
        resolved_payout = 0
        pending_invested = 0
        all_results = []
        trades_summary = []
        
        markets_found_total = 0
        markets_processed_total = 0

        # 1. Determine Date Range
        end_dt = datetime.strptime(target_date, "%Y-%m-%d")
        # Or if the user meant "from now on", we interpret target_date usually as "today".
        # Safe bet: shift start back by 1 day.
        
        # If target_date is today, we want backtest to end yesterday.
        # If target_date is already historical, we might keep it.
        # 0. Handle Aliases Globally
        weather_city = city
        if city.upper() in ["NYC", "NYC.", "NEW YORK CITY"]:
            weather_city = "New York"
        elif city.upper() in ["LA", "L.A."]:
            weather_city = "Los Angeles"

        if is_prediction:
            # Prediction: Start from today
            current_dt = datetime.now()
            # Ensure lookback_days is at least 1 for prediction
            count = max(1, lookback_days)
            date_range = [(current_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]
        else:
            # Backtest: End yesterday
            end_dt = datetime.strptime(target_date, "%Y-%m-%d")
            effective_end_dt = end_dt - timedelta(days=1)
            # Ensure lookback_days is at least 1
            count = max(1, lookback_days)
            date_range = [(effective_end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count - 1, -1, -1)]
        
        if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
            print(f"DEBUG: Mode={'Prediction' if is_prediction else 'Backtest'}, Range={date_range}")

        for current_date in date_range:
            # 2. Discover Market Series
            # Strategy: specific search for "Highest temperature" to cut through noise
            # keys: "Highest temperature in NYC", "Highest temperature in New York"
            # Strategy: Mixed Broad + Specific to ensure maximum recall
            # e.g. "Highest temperature in NYC", "NYC", "New York", "Highest temperature in New York"
            date_obj = datetime.strptime(current_date, "%Y-%m-%d")
            month_name = date_obj.strftime("%B")
            day_num = date_obj.day
            
            queries = {
                f"Highest temperature in {city}", 
                f"Highest temperature in {weather_city}",
                f"{month_name} {day_num} {city} weather",
                f"{month_name} {day_num} {weather_city} weather",
                f"{city} weather",
                f"{weather_city} weather",
                city,
                weather_city
            }
            markets = []
            seen_ids = set()
            if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                print(f"DEBUG: Searching queries: {queries}")
            for q in queries:
                 res = await self.pm_client.gamma_search(q, status="all", limit=500)
                 for m in res:
                     mid = str(m.id)
                     if mid not in seen_ids:
                         markets.append(m)
                         seen_ids.add(mid)
            
            def filter_markets(market_list):
                 date_obj = datetime.strptime(current_date, "%Y-%m-%d")
                 month_name = date_obj.strftime("%B") # e.g. "January"
                 month_abbr = date_obj.strftime("%b") # e.g. "Jan"
                 day_num = date_obj.day
                 
                 date_pattern_full = f"{month_name} {day_num}"
                 date_pattern_abbr = f"{month_abbr} {day_num}"
                 target_year = current_date[:4]  # "2026"
                 
                 # City aliases for filtering
                 city_search_terms = {city.lower(), weather_city.lower()}
                 if city.lower() == "london":
                     city_search_terms.add("london")
                 
                 filtered = []
                 for m in market_list:
                     q_lower = m.question.lower()
                     
                     # 1. City Check: Question must contain the city name
                     if not any(term in q_lower for term in city_search_terms):
                         continue
                         
                     # 2. Date Pattern Check: e.g. "January 26" or "Jan 26"
                     if date_pattern_full not in m.question and date_pattern_abbr not in m.question:
                         continue
                         
                     # 3. Topic Check
                     if "highest temperature" not in q_lower:
                         continue
                         
                     # 4. Year Check: Priority to end_date, fallback to created_at
                     m_year = None
                     if m.end_date and len(m.end_date) >= 4:
                         m_year = m.end_date[:4]
                     elif m.created_at and len(m.created_at) >= 4:
                         # Fallback: if end_date is missing (old markets), check creation year
                         m_year = m.created_at[:4]
                     
                     if m_year and m_year != target_year:
                         continue
                         
                     filtered.append(m)
                     
                 return filtered

            if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                print(f"DEBUG: Found {len(markets)} raw unique markets across all queries.")
                relevant_markets = filter_markets(markets)
                print(f"DEBUG: Found {len(relevant_markets)} relevant markets after filtering.")
            else:
                relevant_markets = filter_markets(markets)
            
            # Fallback if nothing found and alias differs
            if not relevant_markets and city.lower() != weather_city.lower():
                alt_query = f"Highest temperature in {weather_city}"
                # Avoid re-running if we already searched this
                if alt_query not in queries:
                     alt_markets = await self.pm_client.gamma_search(alt_query, status="all", limit=500)
                     relevant_markets = filter_markets(alt_markets)
                     if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                         print(f"DEBUG: Alt search found {len(relevant_markets)} markets.")

            if not relevant_markets:
                if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                    print(f"DEBUG: No relevant markets found for {current_date}.")
                continue

            # Sort markets by temperature threshold
            def get_threshold(q):
                info = self._parse_threshold(q)
                val = info.get("value", 0)
                if "or below" in q.lower(): val -= 0.1
                if "or higher" in q.lower(): val += 0.1
                return val
            relevant_markets.sort(key=lambda x: get_threshold(x.question))

            # Determine if the current date is historical or future
            is_historical = datetime.strptime(current_date, "%Y-%m-%d").date() < datetime.now().date()
            
            actual_weather = None
            weather_error = None

            # Always attempt to fetch weather (handles both historical and forecast)
            secondary_weather = None
            try:
                # Primary weather: ALWAYS Visual Crossing for calculations
                actual_weather = await self._get_weather_data(weather_city, current_date)
                
                # Secondary weather: Tomorrow.io for "Double Check" during predictions
                if is_prediction and self.tm_client:
                    if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                        print(f"DEBUG: [Tomorrow.io] Fetching secondary forecast for {current_date}")
                    secondary_weather = await self.tm_client.get_day_weather(weather_city, current_date)
            except Exception as e:
                print(f"Weather API Error for {current_date}: {e}")
                weather_error = str(e)

            # If we missed weather data for a historical date, we can't probability-check reliably
            if is_historical and not actual_weather:
                if weather_error and "401" in weather_error:
                    # Return partial results found so far + Error
                    return {
                        "city": city,
                        "success": False, 
                        "error": "Visual Crossing API Quota Exceeded (401). Partial results shown.",
                        "trades": all_trades,
                        "markets_found": markets_found_total,
                        "markets_processed": markets_processed_total
                    }
                continue

            # 4. Collect Market Data and Identify Best Entry (Start of Day)
            group_results = []
            prediction_time = datetime.strptime(f"{current_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
            target_ts = int(prediction_time.timestamp())
            resolution_time = datetime.strptime(f"{current_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            time_left = resolution_time - prediction_time
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            time_left_str = f"{hours}h {minutes}m"
            
            markets_found_total += len(relevant_markets)

            for market in relevant_markets:
                parsed_threshold = self._parse_threshold(market.question)
                # Skip invalid markets
                if parsed_threshold["value"] == -999: continue
                
                if not actual_weather:
                    continue

                entry_data = {"price": 0.5, "timestamp": "N/A", "fair_price": 0.0, "edge": -1}
                fair_probs = self._calculate_probabilities(actual_weather, market.question)
                fair_price = fair_probs["probability"]

                if market.clob_token_ids:
                    token_id = market.clob_token_ids[0]
                    history = await self.pm_client.get_price_history(token_id)
                    
                    closest = None
                    min_diff = float('inf')
                    for h in history:
                        h_ts = int(h.get("t", h.get("timestamp", 0)))
                        diff = abs(h_ts - target_ts)
                        if diff < min_diff:
                            min_diff = diff
                            closest = h
                    
                    if is_prediction:
                        # Calculate countdown
                        if market.end_date:
                            try:
                                end_dt = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
                                now_dt = datetime.now(end_dt.tzinfo)
                                diff = end_dt - now_dt
                                if diff.total_seconds() > 0:
                                    days = diff.days
                                    hours, rem = divmod(diff.seconds, 3600)
                                    mins, _ = divmod(rem, 60)
                                    if days > 0:
                                        countdown = f"{days}d {hours}h"
                                    elif hours > 0:
                                        countdown = f"{hours}h {mins}m"
                                    else:
                                        countdown = f"{mins}m"
                                else:
                                    # If end_date is past but market is NOT closed, it's either still trading or awaiting resolution
                                    countdown = "Awaiting Res" if market.closed else "Live*"
                            except:
                                countdown = "N/A"
                        else:
                            countdown = "N/A"
                    else:
                        countdown = "N/A"

                    if is_prediction and current_date == datetime.now().strftime("%Y-%m-%d"):
                        # Use live price for today's prediction
                        price = market.yes_price
                        entry_data = {
                            "price": price,
                            "timestamp": "LIVE",
                            "fair_price": fair_price,
                            "edge": fair_price - price,
                            "countdown": countdown
                        }
                    elif closest:
                        price = float(closest.get("p", closest.get("price", 0.5)))
                        if price > 0:
                            entry_data = {
                                "price": price,
                                "timestamp": datetime.fromtimestamp(int(closest.get("t", closest.get("timestamp", 0)))).strftime("%Y-%m-%d %H:%M"),
                                "fair_price": fair_price,
                                "edge": fair_price - price,
                                "countdown": countdown
                            }

                group_results.append({
                    "market": market,
                    "entry_data": entry_data,
                    "fair_price": fair_price
                })

            if not group_results: continue

            # 5. Identify Potential trades (V2.2: Max 1 YES, Max 1 NO)
            best_bucket = max(group_results, key=lambda x: x["fair_price"])
            potential_yes = []
            potential_no = []
            for item in group_results:
                item_price = item["entry_data"].get("price", 0)
                item_edge = item["entry_data"].get("edge", 0)
                if item_price > 0:
                    if item_edge > 0.02: # YES Edge
                        potential_yes.append({"item": item, "side": "YES", "edge": item_edge, "price": item_price})
                    elif item_edge < -0.02: # NO Edge
                        potential_no.append({"item": item, "side": "NO", "edge": -item_edge, "price": 1.0 - item_price})
            
            # Sort both by absolute edge descending
            potential_yes.sort(key=lambda x: x["edge"], reverse=True)
            potential_no.sort(key=lambda x: x["edge"], reverse=True)
            
            # Select top trades
            selected_trades = []
            if potential_yes:
                selected_trades.append(potential_yes[0])
            
            # For V2 mode, we also allow a NO hedge
            if v2_mode and potential_no:
                # Basic rule: Don't pick the same bucket for YES and NO (contradictory)
                # Usually edge logic prevents this anyway, but let's be safe.
                best_no = potential_no[0]
                if not selected_trades or best_no["item"] is not selected_trades[0]["item"]:
                    selected_trades.append(best_no)
                elif len(potential_no) > 1:
                    selected_trades.append(potential_no[1])
            
            # status check
            is_future = datetime.strptime(current_date, "%Y-%m-%d").date() >= datetime.now().date()

            # 4.5 Detect Official Winner in Group (if any)
            official_winner_label = None
            for item in group_results:
                m = item["market"]
                if m.closed and m.yes_price >= 0.99:
                    # This market resolved to YES officially
                    official_winner_label = m.question.split(" be ")[-1].split(" on ")[0]
                    break

            for item in group_results:
                market = item["market"]
                entry_data = item["entry_data"]
                resolution = self._determine_resolution(actual_weather, market.question)
                
                # Use real resolution if market is closed on Polymarket
                is_real_resolution = False
                if market.closed:
                    # For Yes/No markets, if Yes price is 1.0, resolution is 1.0
                    if market.yes_price >= 0.99:
                        resolution = 1.0
                        is_real_resolution = True
                    elif market.yes_price <= 0.01:
                        resolution = 0.0
                        is_real_resolution = True
                
                bucket_label = market.question.split(" be ")[-1].split(" on ")[0]
                
                status = "RESOLVED"
                if is_future:
                    status = "UNRESOLVED/ACTIVE"
                    res_val = "N/A"
                else:
                    res_val = int(resolution)

                threshold_info = self._parse_threshold(market.question)
                creation_ts = market.created_at.replace("Z", "+00:00")
                creation_date_str = datetime.fromisoformat(creation_ts).strftime("%Y-%m-%d %H:%M")

                is_best_v1 = (item is best_bucket)
                price = entry_data.get("price", 0)
                edge = entry_data.get("edge", 0)
                fair_price = item["fair_price"]
                
                if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                    print(f"DEBUG: Bucket {bucket_label} - Fair: {fair_price:.3f}, Mkt: {price:.3f}, Edge: {edge:.3f}")
                
                 # Check if this bucket/side was selected
                selected_info = next((t for t in selected_trades if t["item"] is item), None)
                trade_side = None
                trade_price = 0
                trade_edge = 0
                
                if selected_info:
                    # V1 constraint: V1 must be YES and must be the BEST bucket
                    if not v2_mode:
                        if selected_info["side"] == "YES" and is_best_v1:
                            trade_side = selected_info["side"]
                            trade_price = selected_info["price"]
                            trade_edge = selected_info["edge"]
                    else:
                        # V2: Take the selected trade (YES or NO)
                        trade_side = selected_info["side"]
                        trade_price = selected_info["price"]
                        trade_edge = selected_info["edge"]

                # Calculate trade metrics
                payout = 0
                pnl = 0
                res_str = "SKIPPED"
                
                if trade_side:
                    if not is_future:
                        shares = self.ALLOCATION_PER_TRADE / trade_price
                        # If side is YES, payout is based on YES resolution (resolution)
                        # If side is NO, payout is based on NO resolution (1.0 - resolution)
                        outcome_res = resolution if trade_side == "YES" else (1.0 - resolution)
                        
                        payout = shares * outcome_res
                        pnl = payout - self.ALLOCATION_PER_TRADE
                        res_str = f"WIN ({trade_side})" if outcome_res > 0.9 else f"LOSS ({trade_side})"
                        
                        total_invested += self.ALLOCATION_PER_TRADE
                        total_payout += payout
                        resolved_invested += self.ALLOCATION_PER_TRADE
                        resolved_payout += payout
                    else:
                        res_str = f"PENDING ({trade_side})"
                        pending_invested += self.ALLOCATION_PER_TRADE
                        total_invested += self.ALLOCATION_PER_TRADE
                
                row_roi = (pnl / self.ALLOCATION_PER_TRADE * 100) if trade_side and not is_future else 0

                # Calculate side-relative metrics for CSV
                csv_prob = item['fair_price']
                csv_price = entry_data.get("price", 0)
                if trade_side == "NO":
                    csv_prob = 1.0 - csv_prob
                    csv_price = 1.0 - csv_price
                
                all_results.append({
                    "Market ID": market.id,
                    "Market Group": f"Highest temperature in {city} on {current_date}?",
                    "Outcome Bucket": bucket_label,
                    "Side": trade_side or "NONE",
                    "Status": status,
                    "Market Creation Date": creation_date_str,
                    "Start of Day Date": prediction_time.strftime("%Y-%m-%d %H:%M"),
                    "Market Resolution Date": resolution_time.strftime("%Y-%m-%d %H:%M"),
                    "Forecast Max Temp (F)": round(actual_weather.get("tempmax", 0), 1), 
                    "Forecast Update Time": actual_weather.get("forecast_time", "N/A"),
                    "Actual Max Temp (F)": round(actual_weather["tempmax"], 1) if not is_future else "PENDING",
                    "Target Fahrenheit": round(threshold_info.get("value"), 1),
                    "Predicted Probability": f"{int(csv_prob * 100)}% ({round(csv_prob, 2)})",
                    "Best Entry Price": round(csv_price, 3),
                    "Ends In": entry_data.get("countdown", "N/A"),
                    "Entry Time": entry_data["timestamp"],
                    "Resolution": res_val,
                    "Resolution Source": "OFFICIAL" if is_real_resolution else "SIMULATED",
                    "Time Till Resolution": time_left_str,
                    "Invested ($)": self.ALLOCATION_PER_TRADE if trade_side else 0,
                    "Payout ($)": round(payout, 2) if trade_side else "N/A",
                    "PnL ($)": round(pnl, 2) if trade_side else "N/A",
                    "ROI (%)": f"{row_roi:.1f}%" if trade_side and not is_future else "N/A",
                    "Is Recommendation": "YES" if is_best_v1 else "NO"
                })

                if is_best_v1 or v2_mode:
                    # Default to simulated weather
                    raw_actual = actual_weather["tempmax"]
                    actual_display = f"{round(raw_actual, 1)}°F"
                    
                    if threshold_info.get("original_unit") == "C":
                        c_val = (raw_actual - 32) * 5/9
                        actual_display = f"{round(c_val, 1)}°C ({round(raw_actual, 1)}°F)"

                    # If official winner exists, OVERRIDE with that value
                    if official_winner_label:
                        # Find the winning market object to parse its threshold
                        win_m = next((i["market"] for i in group_results if i["market"].question.split(" be ")[-1].split(" on ")[0] == official_winner_label), None)
                        # Fallback search if label matching was inexact
                        if not win_m:
                             win_m = next((i["market"] for i in group_results if i["market"].closed and i["market"].yes_price >= 0.99), None)
                        
                        if win_m:
                            win_info = self._parse_threshold(win_m.question)
                            if win_info.get("original_unit") == "C":
                                w_c = win_info["original"]
                                w_f = win_info["value"]
                                actual_display = f"{w_c}°C ({round(w_f, 1)}°F)"
                            else:
                                w_f = win_info["value"]
                                actual_display = f"{round(w_f, 1)}°F"
                            
                            # Add small marker
                            actual_display += "*"

                    # Calculate side-relative probabilities for display
                    dis_prob = item['fair_price']
                    dis_mkt_prob = price
                    if trade_side == "NO":
                        dis_prob = 1.0 - dis_prob
                        dis_mkt_prob = 1.0 - dis_mkt_prob

                    trades_summary.append({
                        "date": current_date,
                        "market_id": market.id,
                        "market_name": market.question,
                        "bucket": bucket_label,
                        "Side": trade_side or "NONE",
                        "target_f": round(threshold_info.get("value"), 0),
                        "target_display": f"{bucket_label} ({round(threshold_info.get('value'), 1)}°F)",
                        "forecast": round(actual_weather.get("tempmax", 0), 1),
                        "forecast_time": actual_weather.get("forecast_time", "N/A"),
                        "actual": actual_display,
                        "prob": f"{int(dis_prob * 100)}%",
                        "market_prob": f"{int(dis_mkt_prob * 100)}%",
                        "price": round(trade_price, 3) if trade_side else round(price, 3),
                        "countdown": entry_data.get("countdown", "N/A"),
                        "result": res_str,
                        "forecast_secondary": round(secondary_weather.get("tempmax", 0), 1) if secondary_weather else "N/A"
                    })

        # 6. Save and Return Summary
        os.makedirs("test-results", exist_ok=True)
        file_type = "prediction" if is_prediction else "backtest"
        csv_file = f"test-results/{city}_{file_type}_{target_date}_lb{lookback_days}.csv"
        
        final_pnl = total_payout - total_invested
        total_roi = (final_pnl / total_invested * 100) if total_invested > 0 else 0
        resolved_roi = ((resolved_payout - resolved_invested) / resolved_invested * 100) if resolved_invested > 0 else 0

        if all_results:
            summary_row = {k: "" for k in all_results[0].keys()}
            summary_row["Market Group"] = "TOTAL SUMMARY"
            summary_row["Invested ($)"] = round(total_invested, 2)
            summary_row["Payout ($)"] = round(total_payout, 2)
            summary_row["PnL ($)"] = round(final_pnl, 2)
            summary_row["ROI (%)"] = round(total_roi, 2)
            all_results.append(summary_row)

            with open(csv_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
                writer.writeheader()
                writer.writerows(all_results)

        return {
            "success": True,
            "city": city,
            "period": f"{date_range[0]} to {date_range[-1]}",
            "total_invested": total_invested,
            "total_payout": total_payout,
            "resolved_invested": resolved_invested,
            "resolved_payout": resolved_payout,
            "resolved_roi": resolved_roi,
            "pending_invested": pending_invested,
            "final_pnl": final_pnl,
            "final_roi": total_roi,
            "csv_path": csv_file,
            "trades": trades_summary,
            "markets_found": markets_found_total,
            "markets_processed": markets_processed_total
        }

    def _parse_threshold(self, question: str) -> Dict[str, Any]:
        """Extract temperature threshold and unit from question."""
        # Check for ranges first (e.g. "14-15°F", "between 10 and 20")
        # Regex for "number - number" where the hyphen is NOT a negative sign for the second number
        # We capture two numbers.
        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-to]\s*(\d+(?:\.\d+)?)", question)
        if range_match:
             val1 = float(range_match.group(1))
             val2 = float(range_match.group(2))
             # Determine unit usually at the end
             unit_match = re.search(r"[0-9]\s*°?([CF])", question, re.IGNORECASE)
             unit = unit_match.group(1).upper() if unit_match else "F"
             
             avg_val = (val1 + val2) / 2
             if unit == 'C':
                return {"value": (avg_val * 9/5) + 32, "unit": "F"}
             return {"value": avg_val, "unit": "F"}

        # Standard single value regex
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?([CF])", question, re.IGNORECASE)
        if not match:
             # Fallback for just a number
             match = re.search(r"(-?\d+(?:\.\d+)?)", question)
             if not match: return {"value": 0.0, "unit": "F"}
             return {"value": float(match.group(1)), "unit": "F"}
        
        val = float(match.group(1))
        unit = match.group(2).upper()
        if unit == 'C':
            f_val = (val * 9/5) + 32
            return {"value": f_val, "unit": "F", "original": val, "original_unit": "C"}
        return {"value": val, "unit": "F"}

    def _calculate_probabilities(self, weather: Dict[str, Any], question: str) -> Dict[str, float]:
        """Heuristic for fair price based on observed weather."""
        threshold_info = self._parse_threshold(question)
        target_val = threshold_info["value"] 
        actual_val = weather.get("tempmax", 0) 
        diff = actual_val - target_val
        
        is_above = "or higher" in question.lower() or "exceed" in question.lower() or "above" in question.lower()
        is_below = "or below" in question.lower() or "below" in question.lower() or "less than" in question.lower()
        is_discrete = not (is_above or is_below)

        if is_discrete:
            abs_diff = abs(diff)
            if abs_diff < 0.5: prob = 0.90
            elif abs_diff < 1.0: prob = 0.70
            elif abs_diff < 1.5: prob = 0.30
            elif abs_diff < 2.0: prob = 0.10
            else: prob = 0.02
        elif is_below:
            if diff < -1.5: prob = 0.98
            elif diff > 1.5: prob = 0.02
            else: prob = 0.5 - (diff / 2.5) # Window of 1.25 degrees each side
        else: # is_above
            if diff > 1.5: prob = 0.98
            elif diff < -1.5: prob = 0.02
            else: prob = 0.5 + (diff / 2.5)
        
        # Ensure bounds
        prob = max(0.01, min(0.99, prob))

        return {"probability": prob, "threshold_f": target_val, "actual_f": actual_val}

    def _determine_resolution(self, weather: Dict[str, Any], question: str) -> float:
        """Determine if the YES token resolved to 1.0 or 0.0."""
        threshold_info = self._parse_threshold(question)
        target_f = threshold_info["value"]
        actual_f = weather.get("tempmax", 0)
        
        is_above = "or higher" in question.lower() or "exceed" in question.lower() or "above" in question.lower()
        is_below = "or below" in question.lower() or "below" in question.lower() or "less than" in question.lower()
        is_discrete = not (is_above or is_below)
        
        if is_discrete:
            return 1.0 if abs(actual_f - target_f) < 1.1 else 0.0
        elif is_below:
            return 1.0 if actual_f <= (target_f + 0.1) else 0.0
        else: # is_above
            return 1.0 if actual_f >= (target_f - 0.1) else 0.0
