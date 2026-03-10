# Market Prediction Engine

This document explains the functionality and usage of the `poly:predict` command. This tool utilizes the same core engine as the Backtest system but leverages **live weather forecasts** to identify trading opportunities in future events.

## Overview

The Prediction Engine scans for upcoming Polymarket weather events (e.g., "Highest temperature in {City} tomorrow") and compares the current market odds against a high-fidelity weather forecast (Visual Crossing). If the forecast suggests a significant probability divergence ("Edge") from the market price, it signals a trade opportunity.

## Methodology

### 1. Data Sources
*   **Polymarket (Gamma API)**: Fetches *live* active markets for future dates.
*   **Visual Crossing Weather API**: Fetches the *forecasted* high temperature for the specific target date.

### 2. Market Selection
For a given `City` and `Lead Time` (NumDays):
1.  The engine calculates the target date (e.g., Today + 1 Day = Tomorrow).
2.  It searches for Polymarket events matching `"Highest temperature in {City} on {Date}"`.
3.  It retrieves the live Order Book (CLOB) to determine the current market price for "YES" shares on various temperature buckets.

### 3. Edge Calculation
The engine compares the Market Probability (Price) vs. the Forecast Probability (Fair Value).

*   **Forecast**: Retrieves the specific forecasted High Temp (e.g., 78°F).
*   **Fair Price**: Calculated heuristically.
    *   If Forecast >> Bucket Threshold -> Fair Price is High (~0.95).
    *   If Forecast << Bucket Threshold -> Fair Price is Low (~0.05).
    *   If Forecast is close to Threshold -> Fair Price is uncertain (~0.50).
*   **Edge**: `Fair Price - Market Price`.

### 4. Signal Generation
*   **Buy Signal**: If `Edge > 0` (The market is underpricing the likelihood of the forecast realizing), the engine recommends a BUY.
*   **Trade Simulation**: It simulates investing a fixed amount ($100) into the best opportunity found.

## CLI Usage

Run market predictions directly from the PolyCode CLI:

```bash
poly:predict <City> <NumDays>
```

*   **City**: The city name (e.g., London, "New York").
*   **NumDays**: How many days into the future to scan.
    *   `1`: Tomorrows weather.
    *   `2`: Day after tomorrow.
    *   ...

### Examples

**1. Predict tomorrow's best trade for London:**
```bash
poly:predict London 1
```

**2. Scan the next 3 days for New York:**
```bash
poly:predict "New York" 3
```

## Output

### Console Output
The CLI displays a "Market Prediction" table:

*   **Target Bucket**: The specific outcome to bet on (e.g., "75F or higher").
*   **Prob**: The model's confidence based on the forecast.
*   **Price**: The current cost to buy YES shares.
*   **Result**: "PENDING" (since the event has not happened yet).

### CSV Report
A CSV file is generated in `test-results/` tracking the prediction:
*   Filename: `{City}_backtest_{Date}_lb{Lookback}.csv`
*   This file can be used later to verify if the prediction was correct once the date passes.
