# Polymarket Autonomous Trading Agent

This document provides a comprehensive overview of the Polymarket autonomous trading agent, a simulation tool designed to execute a weather-based trading strategy on the Polymarket platform. The agent leverages the Tomorrow.io API for weather data and interacts with Polymarket's Gamma API to analyze and trade on weather-related markets.

## 1. Project Overview

The Polymarket trading agent is an extension of the `polycode` CLI, designed to simulate a specific trading strategy that has shown significant returns. The core of the agent is a Python script, `polyagent_cli.py`, which orchestrates the entire process from market scanning to trade execution and portfolio simulation.

### 1.1. Trading Strategy

The agent implements a strategy that capitalizes on under-priced odds in weather-related markets on Polymarket. The logic is as follows:

1.  **Scan Weather Markets**: The agent scans for weather markets on Polymarket, focusing on specific cities (London, New York, Seoul). It filters for markets with low odds (0.1¢ - 10¢) and sufficient liquidity (≥ $50).

2.  **Fetch Weather Forecasts**: For each identified market, the agent fetches the latest weather forecast from the Tomorrow.io API. It calculates the high, low, and average temperatures for the relevant city.

3.  **Calculate Fair Price**: Based on the weather forecast, the agent calculates a "fair price" or probability for the market outcome. This is done by comparing the forecasted temperature to the market's question, with a deviation of ±3.5°F.

4.  **Compare and Trade**: The agent compares the market price to the calculated fair price to determine the "edge." If the edge is significant (i.e., the market is undervalued), the agent simulates a "buy" trade.

### 1.2. Key Performance Indicators

The strategy aims to replicate the success of a similar AI bot that achieved the following target statistics:

| Metric              | Value      |
| ------------------- | ---------- |
| **Predictions**     | 1922       |
| **Biggest Win**     | $7,145     |
| **Biggest ROI**     | 3616%      |
| **Initial Capital** | $197       |
| **Final Capital**   | $7,342     |

## 2. Implementation Details

The agent is built with a modular architecture, with separate components for API interaction, trading strategy, and portfolio simulation.

### 2.1. Project Structure

The key files and directories in the project are:

| File/Directory                  | Description                                                                 |
| ------------------------------- | --------------------------------------------------------------------------- |
| `polyagent_cli.py`              | The main entry point for the trading agent CLI.                             |
| `agent/tools/polymarket_tool.py`  | A client for interacting with the Polymarket Gamma API.                     |
| `agent/tools/weather_tool.py`     | A client for the Tomorrow.io weather API.                                   |
| `agent/tools/trading_strategy.py` | The core trading strategy logic and portfolio simulator.                    |
| `tests/test_polyagent.py`         | Unit and integration tests for the agent.                                   |
| `test-results/`                 | Directory for storing test results and analysis reports.                    |
| `.env.sample.extended`          | A template for the required environment variables, including API keys.      |

### 2.2. Running the Agent

To run the trading agent simulation, execute the following command from the root of the `polycode` directory:

```bash
python3 polyagent_cli.py
```

This will trigger the full analysis and simulation process, and the results will be saved to the `test-results/` directory.

## 3. Polymarket API Integration

The agent interacts with Polymarket through its public APIs. The primary API used is the Gamma API, which provides market data. For more advanced trading, the Order Book API is required.

### 3.1. Gamma Public API

The Gamma API is a free, public API that provides access to market data, including market questions, outcomes, prices, liquidity, and volume. The agent uses this API to scan for weather markets and fetch the necessary data for analysis.

**Endpoint:** `https://gamma-api.polymarket.com/markets`

**Example Request:**

```python
import httpx

async def get_markets():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://gamma-api.polymarket.com/markets?search=weather")
        return response.json()
```

### 3.2. Order Book API

The Order Book API provides access to the order book for each market, showing the bids and asks. This API is necessary for executing trades and requires a Polymarket API key.

**Endpoint:** `https://gamma-api.polymarket.com/order-book/{market_id}`

### 3.3. Obtaining Polymarket API Keys

To access the Order Book API and other authenticated endpoints, you need a Polymarket API key. Here's how to get one:

1.  **Create a Polymarket Account**: If you don't have one already, sign up for an account on [polymarket.com](https://polymarket.com).

2.  **Navigate to Developer Settings**: Once logged in, go to your account settings and find the "Developer" or "API" section.

3.  **Generate an API Key**: Follow the instructions to generate a new API key. You will be provided with a key that you can use to authenticate your API requests.

4.  **Set the Environment Variable**: Store your API key in the `.env` file as `POLYMARKET_API_KEY`:

    ```
    POLYMARKET_API_KEY=your_api_key_here
    ```

## 4. Conclusion

The Polymarket autonomous trading agent provides a powerful simulation of a weather-based trading strategy. By leveraging external data from Tomorrow.io and the rich market data from Polymarket, the agent can identify and capitalize on profitable trading opportunities. The modular design allows for easy extension and adaptation to other trading strategies and markets.

---
*This document was generated by Manus AI.*
