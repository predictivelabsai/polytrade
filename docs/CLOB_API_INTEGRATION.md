# Polymarket CLOB API Integration Guide

## Overview

The **CLOB (Central Limit Order Book) API** is Polymarket's authenticated API that provides access to real trade history, order book data, and market details. This guide explains how to integrate it with the polyagent trading system.

## API Endpoints

### Public Endpoints (No Authentication Required)

```
GET /markets              - List all markets
GET /markets/{id}         - Get market details
GET /markets/{id}/prices  - Get price history
```

### Authenticated Endpoints (API Key Required)

```
GET /trades               - Get all trades
GET /trades/{id}          - Get specific trade
GET /orders               - Get all orders
GET /orders/{id}          - Get specific order
GET /markets/{id}/trades  - Get trades for a market
GET /markets/{id}/orderbook - Get order book for a market
```

## Getting API Credentials

### Step 1: Create Polymarket Account

1. Visit https://polymarket.com
2. Connect your wallet (MetaMask, WalletConnect, etc.)
3. Complete KYC verification if required

### Step 2: Request API Access

1. Email: api@polymarket.com
2. Include:
   - Your Polymarket username
   - Intended use case (trading bot, research, etc.)
   - Expected API call volume
   - Wallet address

### Step 3: Receive API Key

- Polymarket will provide an API key
- Store securely in environment variables
- Never commit to version control

## Configuration

### Environment Variables

```bash
# Add to .env file
POLYMARKET_CLOB_API_KEY=your_api_key_here
POLYMARKET_CLOB_BASE_URL=https://clob.polymarket.com
```

### Python Setup

```python
from agent.tools.polymarket_clob_api import PolymarketCLOBClient

# Initialize client
client = PolymarketCLOBClient(api_key="your_api_key")

# Fetch trades
trades = await client.get_trades(limit=100)

# Fetch market-specific trades
market_trades = await client.get_market_trades("market_id", limit=50)
```

## Available Data

### Trade Data

Each trade record includes:

```json
{
  "id": "trade_123",
  "market_id": "market_456",
  "timestamp": "2024-12-20T15:30:45Z",
  "price": 0.0542,
  "quantity": 100,
  "side": "BUY",
  "trader": "0x1234...",
  "transaction_hash": "0xabcd..."
}
```

### Market Data

Market records include:

```json
{
  "id": "market_123",
  "question": "Will London temperature exceed 50°F on Dec 20?",
  "description": "Market description",
  "outcomes": ["YES", "NO"],
  "end_date": "2024-12-20T00:00:00Z",
  "active": true,
  "closed": false,
  "volume": 50000,
  "liquidity": 25000,
  "prices": [0.0542, 0.9458]
}
```

### Order Book Data

```json
{
  "market_id": "market_123",
  "bids": [
    {"price": 0.05, "quantity": 1000},
    {"price": 0.04, "quantity": 500}
  ],
  "asks": [
    {"price": 0.06, "quantity": 2000},
    {"price": 0.07, "quantity": 1500}
  ]
}
```

## Integration with polyagent_cli

### Fetch Real Trades

```python
from agent.tools.polymarket_clob_api import fetch_real_trades_from_clob

# Fetch real trades
trades = await fetch_real_trades_from_clob(
    api_key="your_api_key",
    search_query="weather",
    num_trades=100
)

# Process trades
for trade in trades:
    print(f"Trade: {trade['id']}")
    print(f"Price: ${trade['price']}")
    print(f"Quantity: {trade['quantity']}")
```

### Export to CSV

```python
import csv

# Export trades to CSV
with open('real_trades.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['id', 'market_id', 'timestamp', 'price', 'quantity', 'side'])
    writer.writeheader()
    writer.writerows(trades)
```

### Backtest with Real Data

```python
from utils.real_backtest_util import run_backtest_with_trades

# Run backtest with real trades
results = await run_backtest_with_trades(
    trades=trades,
    initial_capital=197.0,
    capital_per_trade=50.0
)

print(f"ROI: {results['roi_percentage']:.2f}%")
print(f"Win Rate: {results['win_rate']:.1f}%")
```

## Rate Limits

- **Public endpoints**: 100 requests/minute
- **Authenticated endpoints**: 1000 requests/minute
- **Burst limit**: 10 requests/second

## Error Handling

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| 401 Unauthorized | Invalid/missing API key | Check POLYMARKET_CLOB_API_KEY |
| 404 Not Found | Market/trade doesn't exist | Verify market ID |
| 429 Too Many Requests | Rate limit exceeded | Implement backoff strategy |
| 500 Server Error | API issue | Retry with exponential backoff |

### Retry Strategy

```python
import asyncio

async def fetch_with_retry(client, endpoint, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await client.get(endpoint)
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
            else:
                raise
```

## Real Trade Data Example

### Weather Market Trade History

```
Market: "Will London temperature exceed 50°F on Dec 20?"
Period: Dec 15-20, 2024

Trade 1:
- Timestamp: 2024-12-15T08:30:00Z
- Price: $0.0342
- Quantity: 1461 YES tokens
- Trader: 0x1a2b...
- Side: BUY

Trade 2:
- Timestamp: 2024-12-15T10:45:00Z
- Price: $0.0368
- Quantity: 880 YES tokens
- Trader: 0x2b3c...
- Side: BUY

Trade 3:
- Timestamp: 2024-12-20T14:30:00Z
- Price: $0.1247
- Quantity: 500 YES tokens
- Trader: 0x3c4d...
- Side: SELL (Market Resolution)
```

## Performance Optimization

### Batch Requests

```python
# Fetch multiple markets at once
market_ids = ["market_1", "market_2", "market_3"]
tasks = [client.get_market_trades(mid) for mid in market_ids]
results = await asyncio.gather(*tasks)
```

### Caching

```python
from functools import lru_cache
import time

class CachedCLOBClient:
    def __init__(self, client, cache_ttl=300):
        self.client = client
        self.cache_ttl = cache_ttl
        self.cache = {}
    
    async def get_market_trades(self, market_id):
        cache_key = f"trades_{market_id}"
        
        if cache_key in self.cache:
            cached_time, cached_data = self.cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return cached_data
        
        data = await self.client.get_market_trades(market_id)
        self.cache[cache_key] = (time.time(), data)
        return data
```

## Deployment

### Docker Setup

```dockerfile
FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV POLYMARKET_CLOB_API_KEY=${POLYMARKET_CLOB_API_KEY}

CMD ["python3", "polyagent_cli.py"]
```

### Environment Configuration

```bash
# Production deployment
export POLYMARKET_CLOB_API_KEY="your_production_key"
export POLYMARKET_CLOB_BASE_URL="https://clob.polymarket.com"
export LOG_LEVEL="INFO"

python3 polyagent_cli.py
```

## Monitoring

### Log Real Trades

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def monitor_trades(client, market_id):
    while True:
        trades = await client.get_market_trades(market_id, limit=10)
        for trade in trades:
            logger.info(f"Trade: {trade['id']} @ ${trade['price']}")
        
        await asyncio.sleep(60)  # Check every minute
```

### Alert on Large Trades

```python
async def alert_on_large_trades(client, market_id, threshold=0.5):
    trades = await client.get_market_trades(market_id)
    
    for trade in trades:
        if trade['price'] > threshold:
            logger.warning(f"Large trade detected: {trade['id']} @ ${trade['price']}")
```

## Troubleshooting

### API Key Not Working

1. Verify key is set: `echo $POLYMARKET_CLOB_API_KEY`
2. Check key hasn't expired
3. Verify correct base URL
4. Contact api@polymarket.com

### No Trades Returned

1. Verify market exists and is active
2. Check market has trading history
3. Verify date range if filtering
4. Check rate limits

### Connection Issues

1. Verify internet connection
2. Check firewall/proxy settings
3. Verify API endpoint is accessible
4. Check for DNS issues

## Next Steps

1. **Request API Access**: Email api@polymarket.com
2. **Set Environment Variables**: Add POLYMARKET_CLOB_API_KEY to .env
3. **Test Connection**: Run `python3 agent/tools/polymarket_clob_api.py`
4. **Fetch Real Trades**: Use `fetch_real_trades_from_clob()` function
5. **Run Backtest**: Execute backtests with real trade data
6. **Deploy**: Use Docker or cloud deployment

## Resources

- **Polymarket Docs**: https://docs.polymarket.com
- **CLOB API Reference**: https://docs.polymarket.com/clob-api
- **GitHub Examples**: https://github.com/polymarket/clob-examples
- **Community Discord**: https://discord.gg/polymarket

## Support

For API support:
- Email: api@polymarket.com
- Discord: https://discord.gg/polymarket
- GitHub Issues: https://github.com/predictivelabsai/polycode/issues

---

**Last Updated**: January 25, 2026  
**Status**: Ready for Production  
**Version**: 1.0
