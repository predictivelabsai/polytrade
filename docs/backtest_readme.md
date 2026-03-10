# Real-Data Backtest Engine

This document explains the functionality and usage of the Real-Data Backtest Engine implemented in `utils/backtest_engine.py`. Unlike synthetic backtests, this engine uses **live historical data** from Polymarket (via Gamma API) and Visual Crossing (Weather API) to simulate trading performance on past events.

## Overview

The Backtest Engine simulates how the "Weather Strategy" would have performed on specific days in the past. It reconstructs the market state at the beginning of each day (00:00 UTC) and decides whether to place a trade based on the weather forecast available at that time vs. the market odds.

## Methodology

### 1. Data Sources
*   **Polymarket (Gamma API)**: Used to fetch historical markets and their Order Book (CLOB) price history.
*   **Visual Crossing Weather API**: Used to fetch the *actual* observed weather for the target city and date.

### 2. Market Discovery
For a given `City` and `Lookback Period` (e.g., last 7 days):
1.  The engine searches for relevant Polymarket events matching `"Highest temperature in {City} on {Date}"`.
2.  It filters for "Winner Takes All" markets (Binary) related to temperature thresholds.
3.  Markets are sorted by their temperature threshold (e.g., >50F, >60F, etc.).

### 3. Simulation Logic (Per Day)
For each day in the backtest period:
1.  **Entry Time**: The engine assumes a decision is made at **00:00:00 UTC** on the day of the event.
2.  **Price Fetching**: It queries the historical CLOB prices for the bucket closest to 00:00 UTC.
3.  **Fair Price Calculation**:
    *   It fetches the *actual* daily high temperature (simulating a "perfect forecast" or high-confidence forecast).
    *   It calculates a "Fair Probability" for each bucket based on how far the actual temp is from the bucket's threshold.
    *   *Note: In a real live run, this "actual" temp would be replaced by a Forecast provider.*
4.  **Trade execution**:
    *   It identifies the single "Outcome Bucket" with the highest Edge (Fair Price - Market Price).
    *   If `Edge > 0` and `Price > 0`, it simulates a trade of $100 (fixed allocation).

### 4. PnL Calculation
*   **Win**: If the Actual Temperature satisfies the bucket condition (e.g., >70F and Actual is 75F), the position resolves to $1.00.
*   **Loss**: If the condition is not met, the position resolves to $0.00.
*   **ROI**: Calculated as `(Payout - Cost) / Cost * 100`.

## CLI Usage

You can run backtests directly from the PolyCode CLI using the `poly:backtest` command.

### Command Syntax

```bash
poly:backtest <City> <Days>
```

*   **City**: The city name (e.g., "London", "New York", "Seoul").
*   **Days**: Number of past days to backtest (default: 7).

### Examples

**1. Backtest London for the past week:**
```bash
poly:backtest London 7
```

**2. Backtest New York for the past month:**
```bash
poly:backtest "New York" 30
```

*Note: Quotes are required for cities with spaces.*

## Output & Reports

### Console Output
The CLI displays a summary table of every trade simulated:
*   **Date**: The date of the market.
*   **Target Bucket**: The specific outcome bet on (e.g., "70F or higher").
*   **Prob**: The calculated fair probability.
*   **Price**: The historical price paid.
*   **Result**: WIN/LOSS status.

### CSV Report
A detailed CSV report is saved to `test-results/`:
*   Filename format: `{City}_backtest_{Date}_lb{Days}.csv`
*   Contains full details including exact timestamps, prices, and weather data points.

### Portfolio Summary
A final summary shows:
*   Total Capital Invested
*   Total Payout
*   Net Profit/Loss
*   Return on Investment (ROI)
