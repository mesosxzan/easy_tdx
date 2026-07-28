# ruff: noqa: E501
"""比较退化股票在基线 vs 优化后的交易明细。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategy import Strategy  # noqa: E402
from easy_tdx import Market, Period, Adjust  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

BASELINE_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2_baseline.py"
OPTIMIZED_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"

DEGRADE_STOCKS = [
    ("920725", "BJ"), ("301319", "SZ"), ("603580", "SH"),
    ("600664", "SH"), ("002810", "SZ"), ("600744", "SH"),
]


def load_strategy_class(file_path: Path, module_name: str) -> type[Strategy]:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        try:
            if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                return obj
        except TypeError:
            pass
    raise RuntimeError(f"未找到 Strategy 子类: {file_path}")


def run_backtest(client, code, market_str, strategy_cls):
    market = Market.SH if market_str == "SH" else (Market.BJ if market_str == "BJ" else Market.SZ)
    df = client.get_stock_kline(market, code, period=Period.DAILY, start=0, count=500, adjust=Adjust.QFQ)
    engine = BacktestEngine(strategy=strategy_cls, cash=100_000.0, commission=0.0003)
    return engine.run(df)


def extract_trades(result, code):
    trades_df = result.trades
    if trades_df.empty:
        return []
    all_trades = []
    buy_rows = trades_df[
        (trades_df["direction"] == "BUY") & (~trades_df.get("rejected", False))
    ].copy()
    for _, buy_row in buy_rows.iterrows():
        buy_dt = pd.Timestamp(buy_row["datetime"])
        sell_rows = trades_df[
            (trades_df["direction"] == "SELL")
            & (pd.to_datetime(trades_df["datetime"]) >= buy_dt)
            & (~trades_df.get("rejected", False))
        ]
        if sell_rows.empty:
            continue
        sell_row = sell_rows.iloc[0]
        pnl_pct = (sell_row["price"] - buy_row["price"]) / buy_row["price"] * 100
        all_trades.append({
            "code": code,
            "buy_date": str(buy_row["datetime"])[:10],
            "buy_price": buy_row["price"],
            "sell_date": str(sell_row["datetime"])[:10],
            "sell_price": sell_row["price"],
            "pnl_pct": pnl_pct,
            "buy_reason": buy_row.get("reason", ""),
            "sell_reason": sell_row.get("reason", ""),
        })
    return all_trades


def main() -> None:
    client = MacClient.from_best_host()
    client.connect()

    baseline_cls = load_strategy_class(BASELINE_FILE, "baseline")
    optimized_cls = load_strategy_class(OPTIMIZED_FILE, "optimized")

    for code, market_str in DEGRADE_STOCKS:
        print(f"\n{'='*100}")
        print(f"  {code} ({market_str})")
        print(f"{'='*100}")

        result_b = run_backtest(client, code, market_str, baseline_cls)
        result_o = run_backtest(client, code, market_str, optimized_cls)

        trades_b = extract_trades(result_b, code)
        trades_o = extract_trades(result_o, code)

        print(f"\n  {'#':>2} | {'基线买入':>12} @{'>7'} | {'基线卖出':>12} @{'>7'} | {'收益':>7} | {'卖出原因':>12} || {'优化买入':>12} @{'>7'} | {'优化卖出':>12} @{'>7'} | {'收益':>7} | {'卖出原因':>12}")
        print(f"  {'-'*120}")

        max_len = max(len(trades_b), len(trades_o))
        total_b = 0.0
        total_o = 0.0
        for i in range(max_len):
            tb = trades_b[i] if i < len(trades_b) else None
            to = trades_o[i] if i < len(trades_o) else None

            if tb:
                total_b += tb["pnl_pct"]
                b_str = f"{tb['buy_date']} @{tb['buy_price']:7.2f} | {tb['sell_date']} @{tb['sell_price']:7.2f} | {tb['pnl_pct']:+7.2f}% | {tb['sell_reason']:>12}"
            else:
                b_str = f"{'(无)':>12} {'':>7} | {'':>12} {'':>7} | {'':>7} | {'':>12}"

            if to:
                total_o += to["pnl_pct"]
                o_str = f"{to['buy_date']} @{to['buy_price']:7.2f} | {to['sell_date']} @{to['sell_price']:7.2f} | {to['pnl_pct']:+7.2f}% | {to['sell_reason']:>12}"
            else:
                o_str = f"{'(无)':>12} {'':>7} | {'':>12} {'':>7} | {'':>7} | {'':>12}"

            print(f"  {i+1:>2} | {b_str} || {o_str}")

        print(f"\n  基线总收益: {total_b:+.2f}% | 优化总收益: {total_o:+.2f}% | 变化: {total_o-total_b:+.2f}%")

    client.close()


if __name__ == "__main__":
    main()
