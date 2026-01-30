"""Polymarket API client for fetching market data and weather conditions."""
import httpx
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class PolymarketMarket:
    """Represents a Polymarket market."""
    id: str
    question: str
    description: str
    outcomes: List[str]
    yes_price: float
    no_price: float
    liquidity: float
    volume: float
    created_at: str
    end_date: str
    condition_id: Optional[str] = None
    clob_token_ids: Optional[List[str]] = None
    closed: bool = False
    resolution: Optional[str] = None


@dataclass
class OrderBook:
    """Represents an order book for a market."""
    market_id: str
    bids: List[Dict[str, Any]]
    asks: List[Dict[str, Any]]
    mid_price: float


class PolymarketClient:
    """Client for interacting with Polymarket API."""

    BASE_URL = "https://gamma-api.polymarket.com"
    MARKETS_ENDPOINT = "/markets"
    ORDER_BOOK_ENDPOINT = "/order-book"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Polymarket client.
        
        Args:
            api_key: Optional API key for authenticated endpoints
        """
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

        # Monkeypatch py_clob_client to disable HTTP/2 and increase timeout
        # This fixes "Request exception!" (httpx.RequestError) on many systems (Mac/Cloudflare)
        try:
            import py_clob_client.http_helpers.helpers as helpers
            if not hasattr(helpers, '_fixed_client') or not helpers._fixed_client:
                logger.info("Monkeypatching py_clob_client: Disabling HTTP/2 and increasing timeout.")
                helpers._http_client = httpx.Client(http2=False, timeout=60.0)
                helpers._fixed_client = True
        except ImportError:
            pass

        # Lazy initialize ClobClient for Real Trading
        self.clob_client = None
        self.proxy_address = None

    async def _ensure_clob_client(self):
        """Ensure ClobClient is initialized, automating discovery if needed."""
        if self.clob_client:
            return True

        import os
        from py_clob_client.client import ClobClient, ApiCreds
        
        pk = os.getenv("POLYMARKET_WALLET_PRIVATE_KEY")
        if not pk:
            return False

        try:
            # Check if we have full credentials in .env
            pm_key = os.getenv("POLYMARKET_API_KEY")
            pm_secret = os.getenv("POLYMARKET_SECRET")
            pm_passphrase = os.getenv("POLYMARKET_PASSPHRASE")
            proxy_addr = os.getenv("POLYMARKET_PROXY_ADDRESS")
            sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))

            # If something is missing, start discovery
            if not all([pm_key, pm_secret, pm_passphrase, proxy_addr]):
                logger.info("Credentials missing in .env. Attempting auto-discovery...")
                
                # 1. Derive Signer Address
                temp_client = ClobClient(host="https://clob.polymarket.com", key=pk, chain_id=137)
                signer = temp_client.get_address()
                
                # 2. Discover Proxy Address via Gamma API
                if not proxy_addr:
                    url = f"https://gamma-api.polymarket.com/public-profile"
                    resp = await self.client.get(url, params={"address": signer})
                    if resp.status_code == 200:
                        proxy_addr = resp.json().get("proxyWallet")
                        if proxy_addr:
                            logger.info(f"Discovered Proxy Address: {proxy_addr}")
                            sig_type = 1 # Enable Proxy Mode (POLY_PROXY)
                
                # 3. Create/Derive API Keys
                # Re-initialize with detected proxy to ensure derivation is correct
                self.clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    key=pk,
                    chain_id=137,
                    funder=proxy_addr,
                    signature_type=sig_type
                )
                
                if not pm_key:
                    logger.info("Deriving API credentials from private key...")
                    # create_or_derive_api_creds returns creds
                    creds = self.clob_client.create_or_derive_api_creds()
                    self.clob_client.set_api_creds(creds)
                    logger.info("API credentials successfully derived and set.")
                else:
                    # We had keys but missing proxy, re-apply keys to the proxy-aware client
                    self.clob_client.set_api_creds(ApiCreds(pm_key, pm_secret, pm_passphrase))
            else:
                # Standard full init
                self.clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    key=pk,
                    chain_id=137,
                    creds=ApiCreds(pm_key, pm_secret, pm_passphrase),
                    funder=proxy_addr,
                    signature_type=sig_type
                )
            
            self.proxy_address = proxy_addr
            logger.info("ClobClient Initialized successfully.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to auto-setup Polymarket client: {e}")
            return False

    async def get_portfolio(self) -> Dict[str, Any]:
        """Fetch real on-chain portfolio (USDC + Positions)."""
        if not await self._ensure_clob_client():
            return {"error": "Real trading not configured. Ensure POLYMARKET_WALLET_PRIVATE_KEY is in .env"}
        
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            # 1. Get Collateral (USDC) Balance
            bal_resp = self.clob_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            usdc_balance = float(bal_resp.get("balance", 0)) / 1e6 # USDC is 6 decimals on Polygon
            
            # 2. Get Positions via Data API
            # Endpoint: https://data-api.polymarket.com/positions?user=<proxy>
            proxy = self.proxy_address
            positions = []
            
            if proxy:
                url = f"https://data-api.polymarket.com/positions?user={proxy}"
                resp = await self.client.get(url)
                if resp.status_code == 200:
                    raw_positions = resp.json()
                    
                    # Prepare concurrent fetching for slugs
                    async def resolve_slug(rp):
                        slug = rp.get("slug")
                        short_id = None
                        if slug:
                            try:
                                res = await self.client.get(f"{self.BASE_URL}/markets?slug={slug}")
                                if res.status_code == 200:
                                    m_data = res.json()
                                    if isinstance(m_data, list) and len(m_data) > 0:
                                        short_id = m_data[0].get("id")
                                    elif isinstance(m_data, dict):
                                        short_id = m_data.get("id")
                            except:
                                pass
                        return {**rp, "resolved_market_id": short_id}

                    import asyncio
                    results = await asyncio.gather(*[resolve_slug(rp) for rp in raw_positions])
                    
                    for rp in results:
                        positions.append({
                            "asset": rp.get("asset"), # Token ID
                            "market_id": rp.get("resolved_market_id") or rp.get("conditionId") or rp.get("marketId"), # Prefer resolved ID
                            "market": rp.get("title"), # Question
                            "outcome": rp.get("outcome"),
                            "size": float(rp.get("size", 0)),
                            "entry_price": float(rp.get("avgPrice", 0)),
                            "current_price": float(rp.get("curPrice", 0)),
                            "initial_value": float(rp.get("initialValue", 0)),
                            "current_value": float(rp.get("currentValue", 0)),
                            "pnl": float(rp.get("cashPnl", 0)),
                            "pnl_percent": float(rp.get("percentPnl", 0))
                        })
                else:
                    logger.warning(f"Failed to fetch positions from Data API: {resp.text}")

            return {
                "balance": usdc_balance,
                "positions": positions
            }
            
        except Exception as e:
             logger.error(f"Portfolio Fetch Error: {e}")
             return {"error": str(e)}
    
    async def create_order(self, token_id: str, amount: float, side: str = "BUY") -> Dict[str, Any]:
        """Place a real order on Polymarket CLOB."""
        if not await self._ensure_clob_client():
            raise ValueError("Real trading not configured. Ensure POLYMARKET_WALLET_PRIVATE_KEY is in .env")

        try:
            from py_clob_client.clob_types import OrderType, MarketOrderArgs
            
            # 1. Create the market order (signs it internally)
            order = self.clob_client.create_market_order(
                MarketOrderArgs(
                    token_id=token_id,
                    amount=amount,
                    side=side.upper()
                )
            )
            
            # 2. Post the order to the CLOB
            # For market orders, FOK (Fill or Kill) is recommended for immediate execution
            resp = self.clob_client.post_order(order, orderType=OrderType.FOK)
            
            return {"status": "success", "response": resp}
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Real Trade Failed: {e}\n{error_details}")
            return {
                "status": "error", 
                "message": f"{type(e).__name__}: {str(e)}",
                "details": error_details if os.getenv("FINCODE_DEBUG") == "true" else "See logs for traceback"
            }

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_markets(
        self,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "volume",
        active: bool = True,
        closed: bool = False,
    ) -> List[PolymarketMarket]:
        """Fetch markets from Polymarket."""
        try:
            params = {
                "limit": limit,
                "offset": offset,
                "sortBy": sort_by,
                "active": "true" if active else "false",
                "closed": "true" if closed else "false",
            }
            if search:
                params["search"] = search

            response = await self.client.get(
                f"{self.BASE_URL}{self.MARKETS_ENDPOINT}",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()

            markets_data = data if isinstance(data, list) else data.get("data", [])
            markets = []
            for market_data in markets_data:
                try:
                    market = self._parse_market(market_data)
                    markets.append(market)
                except Exception as e:
                    logger.warning(f"Failed to parse market: {e}")
                    continue

            return markets
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []

    async def get_markets_by_tag(
        self,
        tag_id: str,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> List[PolymarketMarket]:
        """Fetch markets for a specific tag using the events pagination endpoint."""
        try:
            params = {
                "tag_id": tag_id,
                "limit": limit,
                "offset": offset,
                "active": "true" if active else "false",
                "closed": "true" if closed else "false",
                "archived": "false",
            }
            response = await self.client.get(
                f"{self.BASE_URL}/events/pagination",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
            
            all_markets = []
            events = data if isinstance(data, list) else data.get("data", [])
            for event in events:
                markets_data = event.get("markets", [])
                for m_data in markets_data:
                    try:
                        all_markets.append(self._parse_market(m_data))
                    except Exception as e:
                        continue
            
            return all_markets
        except Exception as e:
            logger.error(f"Error fetching markets by tag {tag_id}: {e}")
            return []

    async def gamma_search(self, q: str, status: str = "active", limit: int = 50) -> List[PolymarketMarket]:
        """Search Polymarket using the public-search endpoint (favored for keyword search)."""
        try:
            params = {
                "q": q,
                "events_status": status,
                "limit_per_type": limit
            }
            response = await self.client.get(
                f"{self.BASE_URL}/public-search",
                params=params,
                headers=self.headers
            )
            response.raise_for_status()
            data = response.json()
            
            # Gamma search returns results grouped by type: 'events' containing 'markets'
            events = data.get("events", [])
            all_markets = []
            for event in events:
                event_markets = event.get("markets", [])
                for m in event_markets:
                    all_markets.append(self._parse_market(m))
            
            # Also check for top-level 'markets' if any (sometimes included)
            direct_markets = data.get("markets", [])
            for m in direct_markets:
                all_markets.append(self._parse_market(m))

            return all_markets
        except Exception as e:
            logger.error(f"Error in gamma_search for '{q}': {e}")
            return []

    async def get_market_by_id(self, market_id: str) -> Optional[PolymarketMarket]:
        """Fetch a specific market by ID.
        
        Args:
            market_id: The market ID
            
        Returns:
            PolymarketMarket object or None if not found
        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}{self.MARKETS_ENDPOINT}/{market_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
            return self._parse_market(data)
        except Exception as e:
            logger.error(f"Error fetching market {market_id}: {e}")
            return None

    async def get_order_book(self, market_id: str) -> Optional[OrderBook]:
        """Fetch order book for a market.
        
        Args:
            market_id: The market ID
            
        Returns:
            OrderBook object or None if not found
        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}{self.ORDER_BOOK_ENDPOINT}/{market_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            # Calculate mid price
            mid_price = 0.5
            if bids and asks:
                best_bid = max(float(bid[0]) for bid in bids) if bids else 0
                best_ask = min(float(ask[0]) for ask in asks) if asks else 1
                mid_price = (best_bid + best_ask) / 2

            return OrderBook(
                market_id=market_id,
                bids=bids,
                asks=asks,
                mid_price=mid_price,
            )
        except Exception as e:
            logger.error(f"Error fetching order book for {market_id}: {e}")
            return None

    async def search_weather_markets(
        self,
        cities: Optional[List[str]] = None,
        min_liquidity: float = 50.0,
        max_price: float = 0.10,
    ) -> List[PolymarketMarket]:
        """Search for weather markets with specific criteria.
        
        Args:
            cities: List of cities to search for (e.g., ["London", "New York"])
            min_liquidity: Minimum liquidity threshold
            max_price: Maximum price threshold for YES token
            
        Returns:
            List of matching markets
        """
        markets = []
        cities = cities or ["London", "New York", "Seoul"]

        for city in cities:
            try:
                search_query = f"weather {city} temperature"
                city_markets = await self.get_markets(
                    search=search_query,
                    limit=50,
                )

                for market in city_markets:
                    # Filter by criteria
                    if (
                        market.liquidity >= min_liquidity
                        and market.yes_price <= max_price
                    ):
                        markets.append(market)
            except Exception as e:
                logger.warning(f"Error searching markets for {city}: {e}")
                continue

        return markets

    async def get_price_history(self, market_id: str) -> List[Dict[str, Any]]:
        """Fetch price history for a market with diagnostics."""
        try:
            # Endpoint Candidates
            candidates = [
                {"url": f"https://clob.polymarket.com/prices-history", "params": {"market": market_id, "interval": "max"}},
                {"url": f"https://clob.polymarket.com/prices-history", "params": {"market": market_id}},
                {"url": f"{self.BASE_URL}/prices-history", "params": {"market": market_id}},
                {"url": f"{self.BASE_URL}/prices-history", "params": {"marketId": market_id}},
                {"url": f"{self.BASE_URL}/markets/{market_id}/prices"},
            ]
            
            for cand in candidates:
                url = cand["url"]
                params = cand.get("params")
                try:
                    res = await self.client.get(url, params=params, headers=self.headers)
                    logger.info(f"Trying {url} with {params} -> Status {res.status_code}")
                    if res.status_code == 200:
                        data = res.json()
                        # Handle different response formats
                        if isinstance(data, list):
                            return data
                        if isinstance(data, dict):
                            if "history" in data: return data["history"]
                            if "prices" in data: return data["prices"]
                            return [data]
                except Exception as e:
                    continue
            
            return []
        except Exception as e:
            logger.error(f"Error fetching price history for {market_id}: {e}")
            return []

    async def find_market_id(self, city: str, date: str, extra_query: Optional[str] = None) -> Optional[str]:
        """Attempt to find a market ID for a city, date, and optional threshold.
        
        Args:
            city: City name
            date: Date string (YYYY-MM-DD)
            extra_query: Additional search terms (e.g. "-2°C")
            
        Returns:
            Market ID string or None
        """
        try:
            # Search for closed and active markets
            search_query = f"weather {city} {date}"
            if extra_query:
                search_query += f" {extra_query}"
            
            # Use Gamma search for more relevant results
            markets = await self.gamma_search(search_query, status="all")
            
            if not markets:
                # Fallback to standard markets endpoint
                markets = await self.get_markets(search=search_query, active=False, closed=True)
            
            # Find the best match
            for market in markets:
                # Check if date is in question or endDate matches
                if date in market.question or date in market.end_date:
                    return market.id
                    
            return None
        except Exception as e:
            logger.error(f"Error finding market ID for {city} {date}: {e}")
            return None

    def _parse_market(self, data: Dict[str, Any]) -> PolymarketMarket:
        """Parse raw market data into PolymarketMarket object."""
        
        # Priority 1: lastTradePrice (most accurate for Gamma)
        last_price = data.get("lastTradePrice")
        
        # Priority 2: Midpoint of bestBid/bestAsk
        best_bid = data.get("bestBid")
        best_ask = data.get("bestAsk")
        mid_price = None
        if best_bid is not None and best_ask is not None:
            mid_price = (float(best_bid) + float(best_ask)) / 2
            
        # Priority 3: prices field (fallback)
        prices_raw = data.get("prices", [])
        prices_list = []
        if isinstance(prices_raw, list):
            prices_list = prices_raw
        elif isinstance(prices_raw, str) and prices_raw:
            try:
                prices_list = json.loads(prices_raw)
            except:
                prices_list = []
        
        # Select base YES price
        resolution = data.get("outcome") or data.get("resolutionOutcome")
        closed = data.get("closed", False)
        
        # If closed and resolution is missing, try to derive from outcomePrices (e.g. ["0", "1"])
        if closed and not resolution:
            op_raw = data.get("outcomePrices")
            if op_raw:
                try:
                    if isinstance(op_raw, str): op_prices = json.loads(op_raw)
                    else: op_prices = op_raw
                    
                    if len(op_prices) >= 2:
                        # Index 0 is usually 'Yes', Index 1 is 'No'
                        if str(op_prices[0]) == "1": resolution = "Yes"
                        elif str(op_prices[1]) == "1": resolution = "No"
                        # Fallback to lastTradePrice if available and essentially 0 or 1
                        elif last_price is not None:
                            lp = float(last_price)
                            if lp < 0.01: resolution = "No"
                            elif lp > 0.99: resolution = "Yes"
                except: pass

        if closed and resolution == "Yes":
            yes_price = 1.0
        elif closed and resolution == "No":
            yes_price = 0.0
        elif last_price is not None:
            yes_price = float(last_price)
        elif mid_price is not None:
            yes_price = mid_price
        elif prices_list:
            yes_price = float(prices_list[0])
        else:
            yes_price = 0.5
            
        no_price = 1.0 - yes_price
        
        clob_token_ids = data.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str) and clob_token_ids.strip():
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except:
                clob_token_ids = []

        return PolymarketMarket(
            id=data.get("id", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            outcomes=data.get("outcomes", ["Yes", "No"]),
            yes_price=yes_price,
            no_price=no_price,
            liquidity=float(data.get("liquidity", 0)),
            volume=float(data.get("volume24h", 0)),
            created_at=data.get("createdAt", ""),
            end_date=data.get("endDate", ""),
            condition_id=data.get("conditionId"),
            clob_token_ids=clob_token_ids,
            closed=closed,
            resolution=resolution
        )


# Singleton instance
_client: Optional[PolymarketClient] = None


async def get_polymarket_client(api_key: Optional[str] = None) -> PolymarketClient:
    """Get or create Polymarket client.
    
    Args:
        api_key: Optional API key
        
    Returns:
        PolymarketClient instance
    """
    global _client
    if _client is None:
        _client = PolymarketClient(api_key=api_key)
    return _client
