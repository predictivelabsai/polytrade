import json
import os
from datetime import datetime
from typing import List, Dict, Any

class PortfolioManager:
    """Manages local paper trading portfolio."""

    def __init__(self, storage_path: str = "data/paper_trades.json"):
        self.storage_path = storage_path
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self.trades = self._load_trades()

    def _load_trades(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.storage_path):
            return []
        try:
            with open(self.storage_path, 'r') as f:
                return json.load(f)
        except Exception:
            return []

    def _save_trades(self):
        with open(self.storage_path, 'w') as f:
            json.dump(self.trades, f, indent=2)

    def add_trade(self, market_id: str, question: str, amount: float, price: float, outcome: str = "YES"):
        """Record a new paper trade."""
        trade = {
            "id": f"T-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": question,
            "outcome": outcome,
            "amount": amount,
            "entry_price": price,
            "shares": amount / price if price > 0 else 0,
            "status": "OPEN",
            "payout": 0,
            "exit_price": None
        }
        self.trades.append(trade)
        self._save_trades()
        return trade

    def get_trades(self) -> List[Dict[str, Any]]:
        return self.trades

    def update_trade_status(self, market_id: str, status: str, payout: float = 0):
        """Update status for all trades of a specific market."""
        changed = False
        for t in self.trades:
            if t["market_id"] == market_id and t["status"] == "OPEN":
                t["status"] = status
                t["payout"] = payout
                changed = True
        if changed:
            self._save_trades()

    def close_trade_by_id(self, trade_id: str, exit_price: float) -> Optional[Dict[str, Any]]:
        """Close a specific trade by its transaction ID."""
        for t in self.trades:
            # Handle full ID or short ID suffix
            if t["id"] == trade_id or t["id"].endswith(trade_id):
                if t["status"] == "OPEN":
                    t["status"] = "SOLD"
                    t["exit_price"] = exit_price
                    t["payout"] = t["shares"] * exit_price
                    self._save_trades()
                    return t
        return None
    def sell_shares(self, market_id: str, outcome: str, shares_to_sell: float, exit_price: float) -> List[Dict[str, Any]]:
        """
        Sell a specific number of shares for a market/outcome using FIFO.
        Returns the list of trades that were closed or modified.
        """
        closed_trades = []
        shares_remaining = shares_to_sell
        
        # Filter relevant open trades, sorted by timestamp (FIFO)
        open_trades = [t for t in self.trades if t["market_id"] == market_id and t.get("outcome", "YES") == outcome and t["status"] == "OPEN"]
        open_trades.sort(key=lambda x: x["timestamp"])
        
        for trade in open_trades:
            if shares_remaining <= 0:
                break
                
            available_shares = trade["shares"]
            
            if available_shares <= shares_remaining:
                # Close entire trade
                trade["status"] = "SOLD"
                trade["exit_price"] = exit_price
                trade["payout"] = available_shares * exit_price
                closed_trades.append(trade)
                shares_remaining -= available_shares
            else:
                # Partial close: Split the trade
                # 1. Update current trade to represent the remaining shares
                shares_sold = shares_remaining
                shares_left = available_shares - shares_sold
                
                # Create a new "SOLD" trade record for the portion sold
                sold_trade = trade.copy()
                sold_trade["id"] = f"{trade['id']}-SOLD-{int(datetime.now().timestamp())}"
                sold_trade["shares"] = shares_sold
                sold_trade["amount"] = shares_sold * trade["entry_price"] # Pro-rated entry amount
                sold_trade["status"] = "SOLD"
                sold_trade["exit_price"] = exit_price
                sold_trade["payout"] = shares_sold * exit_price
                sold_trade["parent_id"] = trade["id"]
                
                # Update original trade
                trade["shares"] = shares_left
                trade["amount"] = shares_left * trade["entry_price"]
                
                self.trades.append(sold_trade)
                closed_trades.append(sold_trade)
                shares_remaining = 0
                
        self._save_trades()
        return closed_trades
