# PolyCode - Polymarket Research Agent (Python)

A Polymarket-centric research agent built with LangGraph for terminal UI. Combines prediction market analysis, weather market trading, financial research tools, and Bloomberg-style commands in one CLI.

## Overview

PolyCode is an autonomous research agent focused on Polymarket prediction markets. It thinks, plans, and learns as it works — performing analysis using task planning, self-reflection, and real-time market data.

## Demo

### Interactive CLI
*Using news, financials, and quote tools*
![CLI Query](img/news_tool.png)

*Agent reasoning and final answer*
![CLI Response](img/cli_response.png)

**Key Capabilities:**
- **Polymarket Integration**: Search markets, simulate CLOB trades, run weather market backtests
- **Intelligent Task Planning**: Automatically decomposes complex queries into structured research steps
- **Autonomous Execution**: Selects and executes the right tools to gather data
- **Self-Validation**: Checks its own work and iterates until tasks are complete
- **Real-Time Financial Data**: Access to income statements, balance sheets, and cash flow statements
- **Multi-Provider LLM Support**: OpenAI, Anthropic, Google, xAI, and Ollama

## Architecture

PolyCode uses a ReAct (Reasoning + Acting) pattern with the following components:

```
User Query
    ↓
Agent Planning (LLM decides what to do)
    ↓
Tool Selection & Execution (polymarket, financial_search, web_search)
    ↓
Result Analysis
    ↓
Final Answer Synthesis
```

### Core Components

- **Agent** (`agent/agent.py`): Main orchestrator implementing ReAct pattern
- **LLMProvider** (`model/llm.py`): Multi-provider LLM abstraction
- **Tools** (`tools/financial_search.py`): Financial and web search capabilities
- **Events** (`agent/types.py`): Real-time event streaming for UI updates
- **UI** (`components/app.py`): Textual framework for terminal interface

## Prerequisites

- Python 3.8+
- API Keys:
  - **XAI API Key** (required for Grok models)
  - OpenAI API Key (optional, for GPT models)
  - Anthropic API Key (optional, for Claude models)
  - Google API Key (optional, for Gemini models)
  - Financial Datasets API Key (optional, for financial data)
  - Tavily API Key (optional, for web search)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/predictivelabsai/polycode.git
cd polycode
```

2. Install dependencies:

Option A: Using pip
```bash
pip install -r requirements.txt
```

Option B: Using uv (Recommended for speed)
```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env and add your API keys
```

## Configuration

Edit `.env` to configure:

```env
# LLM Provider Configuration
MODEL=grok-3
MODEL_PROVIDER=xai

# API Keys
XAI_API_KEY=your-xai-api-key
OPENAI_API_KEY=your-openai-api-key
FINANCIAL_DATASETS_API_KEY=your-financial-datasets-api-key
TAVILY_API_KEY=your-tavily-api-key
```

## Usage

Run the agent in your preferred mode:

**Option 1: Full Textual TUI (Recommended)**
```bash
python3 polycode_tui.py
```

**Option 2: Simple CLI**
```bash
python3 polycode.py
```

Or run tests:

```bash
python3 tests/test_xai_integration.py
```

## Example Queries

Try asking PolyCode questions like:

- "What are the top Polymarket weather markets right now?"
- "What was Apple's revenue growth over the last 4 quarters?"
- "Compare Microsoft and Google's operating margins for 2023"
- "Analyze Tesla's cash flow trends over the past year"

## Global Shortcuts (Direct Commands)

The CLI supports fast, "bash-style" direct commands that bypass the AI agent for instant data access.

### Basic Commands

| Command | Description | Speed |
|---------|-------------|-------|
| `load <ticker>` | Direct profile lookup via Massive API | **Instant** |
| `news [ticker]` | Direct news lookup via Tavily | **Instant** |
| `financials [ticker]`| Direct income statement retrieval | **Instant** |
| `quote [ticker]` | Quick market data snapshot | **Instant** |
| `poly:weather [city]` | Search Polymarket weather markets by city/keyword | **Instant** |
| `poly:backtest [type] [period]` | Run real-data backtest on weather markets | Slow |
| `poly:buy <amt> <id>` | Simulate CLOB trade for a market | **Instant** |
| `reset`, `r`, `..` | Reset context/ticker | - |
| `help`, `h`, `?` | Displays help menu | - |
| `cls` | Clear screen | - |
| `exit`, `q` | Quit application | - |

### Bloomberg Terminal Commands

| Command | Description | Speed |
|---------|-------------|-------|
| `des [ticker]` | **Description** - Company profile & key stats | **Instant** |
| `fa [ticker]` | **Financial Analysis** - All statements (income, balance, cash flow) | **Instant** |
| `anr [ticker]` | **Analyst Recommendations** - Buy/Hold/Sell consensus | **Instant** |
| `ee [ticker]` | **Earnings Estimates** - Consensus forecasts | **Instant** |
| `rv [ticker]` | **Relative Valuation** - Peer comparison | **Instant** |
| `own [ticker]` | **Ownership** - Market cap & shares outstanding | **Instant** |
| `gp [ticker]` | **Price Graph** - Historical OHLCV data | **Instant** |
| `gip [ticker]` | **Intraday Graph** - Today's price action | **Instant** |

**Examples:**
```bash
# Bloomberg-style analysis
load AAPL        # Set context to Apple
des              # View company description
fa               # View all financial statements
anr              # View analyst ratings
gp               # View price graph

# Weather market searches
poly:weather London
poly:weather "temperature New York"
poly:weather     # Shows all weather opportunities

# Simulated trading
poly:buy 50 market_token_id_here
```

> [!TIP]
> Use `load AAPL` to set the context, then simply type `news` or `financials` for instant results. Any other input will be handled by the full AI Research Agent (LangGraph) for deep analysis.

## Architecture

PolyCode uses a modular architecture built on **LangGraph** for robust state management and deterministic agentic flows.

```mermaid
graph TD
    subgraph UI ["User Interfaces"]
        TUI["polycode_tui.py (Textual)"]
        CLI["polycode.py (Rich)"]
    end

    Router["CommandProcessor (Router)"]

    subgraph FastPath ["Fast Path (Bash-style)"]
        DirectExec["Direct Tool Execution"]
    end

    subgraph Core ["AI Agent Loop (LangGraph)"]
        Graph["StateGraph"]
        ModelNode["call_model node"]
        ToolNode["execute_tools node"]
        FinalNode["generate_final_answer node"]

        Graph --> ModelNode
        ModelNode -->|Tool Call| ToolNode
        ToolNode --> ModelNode
        ModelNode -->|Complete| FinalNode
    end

    subgraph Tools ["Research Tools"]
        FinTools["Financials / Ticker / Web"]
        NewsTool["News (xAI + Tavily)"]
    end

    subgraph Providers ["LLM Providers"]
        XAI["xAI (Grok-4 / Grok-3)"]
        Others["OpenAI / Claude / Gemini"]
    end

    TUI --> Router
    CLI --> Router

    Router -->|Shortcut| DirectExec
    Router -->|Query| Graph

    DirectExec --> Tools
    ToolNode --> Tools

    ModelNode <--> Providers
    FinalNode <--> Providers
```

### Key Components

1.  **Orchestration**: LangGraph manages the agent's state transitions, ensuring that tool results are correctly fed back into the reasoning process until a final answer is synthesized.
2.  **State Management**: The `AgentState` tracks conversation history, research scratchpads, and summarized tool results to maintain context across multiple iterations.
3.  **Extensible Tools**: The system is designed to easily incorporate new data sources, such as specialized alpha generators or real-time news feeds.
4.  **Provider Agnostic**: Seamlessly switch between different LLM backends using environment variables.

## Project Structure

```
polycode/
├── agent/
│   ├── agent.py          # Core LangGraph orchestrator
│   ├── types.py          # Type definitions
│   ├── prompts.py        # Prompt templates
│   ├── tools/            # Modular tools (Financials, News, Polymarket, etc.)
│   └── __init__.py
├── model/
│   ├── llm.py            # Multi-provider LLM abstraction
│   └── __init__.py
├── components/
│   ├── app.py            # Textual UI application
│   ├── cli.py            # CLI UI application
│   ├── command_processor.py # Direct command handling
│   └── __init__.py
├── polycode_tui.py       # TUI entry point
├── polycode.py           # CLI entry point
├── tests/
│   ├── test_agent_smoke.py      # Runtime verification
│   ├── polycode_cli_test.py     # CLI reproduction test
│   └── massive_test.py          # API provider test
├── requirements.txt             # Python dependencies
├── .env                         # Environment configuration
└── README.md                    # This file
```

## Supported Models

### OpenAI
- gpt-4.1-mini
- gpt-4.1-nano
- gpt-4-turbo
- gpt-3.5-turbo

### xAI (Grok)
- grok-3
- grok-2

### Anthropic (Claude)
- claude-3-sonnet-20240229
- claude-3-opus-20240229

### Google (Gemini)
- gemini-2.5-flash
- gemini-pro

### Ollama (Local)
- llama2
- mistral
- neural-chat

## Changing Models

Set the `MODEL` and `MODEL_PROVIDER` environment variables:

```bash
export MODEL=grok-3
export MODEL_PROVIDER=xai
python3 polycode_tui.py  # or python3 polycode.py
```

## Test Results

Run the integration test suite:

```bash
python3 tests/test_xai_integration.py
```

Results are saved to `test-results/` directory in JSON format.

### Standalone Weather Search

You can also run the weather market search as a standalone script:

```bash
# Search for London weather markets (default)
python tests/test_weather_search.py

# Search for a specific city
python tests/test_weather_search.py "New York"

# Search with custom keywords
python tests/test_weather_search.py Seoul --query "temperature"
```

Results are displayed in a formatted table and saved to `test-results/weather_search_[city]_[timestamp].json`.

## API Integration Details

### XAI (Grok) Integration

The implementation uses OpenAI-compatible API endpoint for xAI:

```python
from model.llm import LLMProvider

llm = LLMProvider.get_model(
    model="grok-3",
    provider="xai",
    temperature=0.7
)
```

**Endpoint**: `https://api.x.ai/v1`
**Authentication**: Bearer token via `XAI_API_KEY` environment variable

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

Please keep pull requests small and focused.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built with [LangChain](https://js.langchain.com), [Textual](https://textual.textualize.io), and [xAI API](https://console.x.ai)
- Predictive Labs AI

## Support

For issues, questions, or suggestions, please open an issue on GitHub.
