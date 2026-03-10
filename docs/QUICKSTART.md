# Polymarket Trading Agent - Quick Start Guide

## Installation

```bash
# Clone the repository
git clone -b polyagent https://github.com/predictivelabsai/polycode.git
cd polycode

# Install dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx
```

## Configuration

```bash
# Copy environment template
cp .env.sample.extended .env

# Edit .env and add your API keys
# Required: TOMORROWIO_API_KEY
# Optional: POLYMARKET_API_KEY
```

## Running the Agent

```bash
# Run the trading agent simulation
python3 polyagent_cli.py

# Run tests
python -m pytest tests/test_polyagent.py -v
```

## Configuration Options

Edit `.env` to customize:

```
# Strategy Parameters
MIN_LIQUIDITY=50.0          # Minimum market liquidity in dollars
MIN_EDGE=0.15               # Minimum edge percentage (15%)
MAX_PRICE=0.10              # Maximum YES token price (10 cents)
MIN_CONFIDENCE=0.60         # Minimum confidence threshold (60%)
INITIAL_CAPITAL=197.0       # Starting capital in dollars

# Cities to analyze
CITIES=London,New York,Seoul
```

## Output

Results are saved to `test-results/` directory:
- `polyagent_analysis_YYYYMMDD_HHMMSS.json` - Detailed analysis results
- Includes market opportunities, trading signals, and portfolio summary

## API Keys

### Tomorrow.io (Required)
1. Visit https://www.tomorrow.io/weather-api/
2. Sign up for free account
3. Copy API key to `.env`: `TOMORROWIO_API_KEY=your_key`

### Polymarket (Optional)
1. Create account at https://polymarket.com
2. Go to Developer settings
3. Generate API key
4. Add to `.env`: `POLYMARKET_API_KEY=your_key`

## Testing

```bash
# Run all tests
python -m pytest tests/test_polyagent.py -v

# Run specific test class
python -m pytest tests/test_polyagent.py::TestTradingStrategy -v

# Run with coverage
python -m pytest tests/test_polyagent.py --cov=agent.tools
```

## Troubleshooting

**ImportError: No module named 'langchain_openai'**
```bash
pip install -r requirements.txt
```

**API Key errors**
- Ensure `.env` file exists and has correct API keys
- Check Tomorrow.io key is valid and has API access enabled

**No markets found**
- Check internet connection
- Verify Polymarket API is accessible
- Check market filtering criteria in configuration

## Documentation

- `docs/polymarket_readme.md` - Full API documentation
- `IMPLEMENTATION_SUMMARY.md` - Technical implementation details
- `tests/test_polyagent.py` - Test examples and usage patterns

## Support

For issues or questions:
1. Check the documentation
2. Review test cases for usage examples
3. Check git commit history for implementation details
