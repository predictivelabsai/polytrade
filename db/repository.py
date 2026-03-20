"""CRUD operations for the polycode database (runs, trades, pnl_snapshots)."""
import json
import uuid
from typing import Any, Dict, List, Optional

from db.connection import get_pool, _record_to_dict


# ─────────────────────────────────────────────────────────────────────────────
# Runs
# ─────────────────────────────────────────────────────────────────────────────

async def create_run(query: str, model: str, provider: str) -> str:
    """Insert a new agent-run record; return the run_id string."""
    pool = await get_pool()
    run_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO runs (run_id, query, model, provider, status)
        VALUES ($1, $2, $3, $4, 'running')
        """,
        run_id, query, model, provider,
    )
    return run_id


async def finish_run(
    run_id: str,
    iterations: int,
    tool_calls: List[Dict],
    error: Optional[str] = None,
) -> None:
    """Mark a run as completed or failed."""
    pool = await get_pool()
    status = "failed" if error else "completed"
    await pool.execute(
        """
        UPDATE runs
        SET status=$1, iterations=$2, tool_calls=$3,
            finished_at=NOW(), error_message=$4
        WHERE run_id=$5
        """,
        status, iterations, json.dumps(tool_calls), error, run_id,
    )


async def get_runs(limit: int = 20) -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT $1", limit
    )
    return [_record_to_dict(r) for r in rows]


async def get_run(run_id: str) -> Optional[Dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM runs WHERE run_id=$1", run_id)
    return _record_to_dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Trades
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_trade(trade: Dict[str, Any]) -> Dict:
    """Insert or update a trade record."""
    pool = await get_pool()

    def _f(key, default=None):
        v = trade.get(key, default)
        return float(v) if v is not None and key in (
            "amount", "entry_price", "shares", "exit_price",
            "payout", "pnl", "edge_pct", "confidence",
        ) else v

    await pool.execute(
        """
        INSERT INTO trades (
            trade_id, run_id, market_id, market_question, trade_side,
            amount, entry_price, shares, status, exit_price, payout, pnl,
            period, city, signal, edge_pct, confidence, trade_type,
            created_at, updated_at
        ) VALUES (
            $1,$2,$3,$4,$5, $6,$7,$8,$9,$10,$11,$12, $13,$14,$15,$16,$17,$18, NOW(),NOW()
        )
        ON CONFLICT (trade_id) DO UPDATE SET
            status      = EXCLUDED.status,
            exit_price  = EXCLUDED.exit_price,
            payout      = EXCLUDED.payout,
            pnl         = EXCLUDED.pnl,
            trade_type  = EXCLUDED.trade_type,
            updated_at  = NOW()
        """,
        trade.get("trade_id"),
        trade.get("run_id"),
        trade.get("market_id", ""),
        trade.get("market_question"),
        trade.get("trade_side"),
        _f("amount", 0),
        _f("entry_price", 0),
        _f("shares", 0),
        trade.get("status", "OPEN"),
        _f("exit_price"),
        _f("payout", 0),
        _f("pnl", 0),
        trade.get("period"),
        trade.get("city"),
        trade.get("signal"),
        _f("edge_pct"),
        _f("confidence"),
        trade.get("trade_type", "paper"),
    )
    return trade


async def get_trades(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    run_id: Optional[str] = None,
    trade_type: Optional[str] = None,
) -> List[Dict]:
    pool = await get_pool()
    conditions: List[str] = []
    params: List[Any] = []

    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if run_id:
        params.append(run_id)
        conditions.append(f"run_id = ${len(params)}")
    if trade_type:
        params.append(trade_type)
        conditions.append(f"trade_type = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params += [limit, offset]
    rows = await pool.fetch(
        f"""
        SELECT * FROM trades {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)-1} OFFSET ${len(params)}
        """,
        *params,
    )
    return [_record_to_dict(r) for r in rows]


async def get_trade(trade_id: str) -> Optional[Dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM trades WHERE trade_id=$1", trade_id)
    return _record_to_dict(row) if row else None


async def update_trade_status(
    trade_id: str,
    status: str,
    exit_price: Optional[float] = None,
    payout: Optional[float] = None,
    pnl: Optional[float] = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE trades
        SET status=$1, exit_price=$2, payout=$3, pnl=$4, updated_at=NOW()
        WHERE trade_id=$5
        """,
        status, exit_price, payout, pnl, trade_id,
    )


async def save_backtest_trades(
    run_id: Optional[str],
    backtest_result: Dict[str, Any],
    city: str,
) -> int:
    """Batch-save trades from a BacktestEngine result into DB with trade_type='backtest'.

    Returns the number of trades saved.
    """
    trades_list = backtest_result.get("trades", [])
    saved = 0
    for t in trades_list:
        side = t.get("Side", "NONE")
        if side == "NONE":
            continue  # skip non-traded rows

        price = float(t.get("price", 0))
        if price <= 0:
            continue

        amount = 100.0  # BacktestEngine uses ALLOCATION_PER_TRADE = 100
        shares = amount / price if price > 0 else 0

        result_str = t.get("result", "")
        if "WIN" in result_str:
            status = "RESOLVED"
            payout = shares * 1.0  # winning shares pay $1 each
        elif "LOSS" in result_str:
            status = "RESOLVED"
            payout = 0.0
        elif "PENDING" in result_str:
            status = "OPEN"
            payout = 0.0
        else:
            status = "OPEN"
            payout = 0.0

        pnl = payout - amount if status == "RESOLVED" else 0.0
        trade_id = f"BT-{t.get('market_id', 'unk')[:20]}-{t.get('date', '')}"

        await upsert_trade({
            "trade_id": trade_id,
            "run_id": run_id,
            "market_id": str(t.get("market_id", "")),
            "market_question": t.get("market_name", ""),
            "trade_side": side,
            "amount": amount,
            "entry_price": price,
            "shares": shares,
            "status": status,
            "payout": payout,
            "pnl": pnl,
            "period": t.get("date"),
            "city": city,
            "signal": side,
            "trade_type": "backtest",
        })
        saved += 1
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# PnL Snapshots
# ─────────────────────────────────────────────────────────────────────────────

async def get_pnl_summary() -> Dict:
    """Return current aggregate PnL (not persisted)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                        AS total_trades,
            COUNT(*) FILTER (WHERE status = 'OPEN')        AS open_trades,
            COUNT(*) FILTER (WHERE status != 'OPEN')       AS closed_trades,
            COALESCE(SUM(amount), 0)                        AS total_invested,
            COALESCE(SUM(payout) FILTER (WHERE status != 'OPEN'), 0)  AS total_payout,
            COALESCE(SUM(pnl)    FILTER (WHERE status != 'OPEN'), 0)  AS realized_pnl,
            COUNT(*) FILTER (WHERE pnl > 0)                AS win_count,
            COUNT(*) FILTER (WHERE pnl < 0)                AS loss_count
        FROM trades
        """
    )
    if not row:
        return {}

    closed         = int(row["closed_trades"] or 0)
    win_count      = int(row["win_count"]     or 0)
    win_rate       = round((win_count / closed * 100) if closed > 0 else 0, 2)
    total_invested = float(row["total_invested"] or 0)
    realized_pnl   = float(row["realized_pnl"]   or 0)
    roi_pct        = round((realized_pnl / total_invested * 100) if total_invested > 0 else 0, 2)

    return {
        "total_trades":   int(row["total_trades"]   or 0),
        "open_trades":    int(row["open_trades"]    or 0),
        "closed_trades":  closed,
        "total_invested": total_invested,
        "total_payout":   float(row["total_payout"] or 0),
        "realized_pnl":   realized_pnl,
        "win_count":      win_count,
        "loss_count":     int(row["loss_count"]     or 0),
        "win_rate":       win_rate,
        "roi_pct":        roi_pct,
    }


async def save_pnl_snapshot(run_id: Optional[str] = None) -> Dict:
    """Calculate current PnL and persist a snapshot row."""
    pool   = await get_pool()
    summary = await get_pnl_summary()

    snap_id = await pool.fetchval(
        """
        INSERT INTO pnl_snapshots (
            run_id, total_invested, total_payout, realized_pnl,
            open_trades, closed_trades, win_count, loss_count,
            win_rate, total_trades, roi_pct
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING snapshot_id
        """,
        run_id,
        summary.get("total_invested", 0),
        summary.get("total_payout",   0),
        summary.get("realized_pnl",   0),
        summary.get("open_trades",    0),
        summary.get("closed_trades",  0),
        summary.get("win_count",      0),
        summary.get("loss_count",     0),
        summary.get("win_rate",       0),
        summary.get("total_trades",   0),
        summary.get("roi_pct",        0),
    )
    return {"snapshot_id": snap_id, **summary, "run_id": run_id}


async def get_pnl_snapshots(limit: int = 100) -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM pnl_snapshots ORDER BY snapshot_time DESC LIMIT $1", limit
    )
    return [_record_to_dict(r) for r in rows]
