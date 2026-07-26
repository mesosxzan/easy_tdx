"""优化前后回测对比脚本。

运行优化后的策略，与基线数据对比，验证盈利锁定阶梯的效果。
"""

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd  # noqa: E402

from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategy import Strategy  # noqa: E402
from easy_tdx.cli.parsers import (  # noqa: E402
    parse_adjust,
    parse_market,
    parse_period,
)
from easy_tdx.mac.client import MacClient  # noqa: E402

STRATEGY_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"
KLINE_COUNT = 1000
CASH = 100_000.0
COMMISSION = 0.0003

STOCK_POOL: list[tuple[str, str, str]] = [
    ("600206", "SZ", "震荡上行"),
    ("002407", "SZ", "大波动"),
    ("002156", "SZ", "趋势"),
    ("600539", "SH", "大牛股"),
    ("000001", "SZ", "银行阴跌"),
    ("600519", "SH", "白酒慢牛"),
    ("300750", "SZ", "新能源"),
    ("002475", "SZ", "消费电子"),
]

# 基线数据（优化前，68 笔交易）
BASELINE = {
    "trades": 68,
    "wins": 31,
    "losses": 37,
    "win_rate": 45.6,
    "total_profit": 415.05,
    "total_loss": 148.34,
    "net": 266.71,
    "pf": 2.80,
    "avg_win": 13.39,
    "avg_loss": 4.01,
}


def _load_strategy_class(file_path: Path) -> type[Strategy]:
    """从文件加载 Strategy 子类。"""
    spec = importlib.util.spec_from_file_location("strategy_module", file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载策略文件: {file_path}")
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


def _print_row(label: str, baseline: float, optimized: float, fmt: str) -> None:
    """打印一行基线 vs 优化对比数据。"""
    diff = optimized - baseline
    if fmt == "d":
        print(f"{label:<12} {int(baseline):>10} {int(optimized):>10} {int(diff):>+10}")
    else:
        print(f"{label:<12} {baseline:>10{fmt}} {optimized:>10{fmt}} {diff:>+10{fmt}}")


def main() -> None:
    print("=" * 70)
    print("优化后回测对比（盈利锁定阶梯）")
    print(f"策略: {STRATEGY_FILE.name} | K线: {KLINE_COUNT} | 股票: {len(STOCK_POOL)} 只")
    print("=" * 70)

    strategy_cls = _load_strategy_class(STRATEGY_FILE)

    print("\n正在连接行情服务器...")
    client = MacClient.from_best_host()
    client.connect()

    all_trades: list[dict] = []

    try:
        for code, market_str, stock_type in STOCK_POOL:
            try:
                market = parse_market(market_str)
                df = client.get_stock_kline(
                    market,
                    code,
                    period=parse_period("DAILY"),
                    start=0,
                    count=KLINE_COUNT,
                    adjust=parse_adjust("QFQ"),
                )
                engine = BacktestEngine(strategy=strategy_cls, cash=CASH, commission=COMMISSION)
                result = engine.run(df)
                trades_df = result.trades
                if trades_df.empty:
                    print(f"  {code} ({stock_type}): 0 笔交易")
                    continue
                buy_rows = trades_df[
                    (trades_df["direction"] == "BUY") & (~trades_df.get("rejected", False))
                ].copy()
                trade_count = 0
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
                    buy_price = float(buy_row["price"])
                    sell_price = float(sell_row["price"])
                    return_pct = (sell_price - buy_price) / buy_price * 100
                    reason = str(sell_row.get("reason", "")) or ""
                    all_trades.append({
                        "code": code,
                        "stock_type": stock_type,
                        "buy_date": str(buy_row["datetime"]),
                        "sell_date": str(sell_row["datetime"]),
                        "buy_price": round(buy_price, 2),
                        "sell_price": round(sell_price, 2),
                        "reason": reason,
                        "return_pct": round(return_pct, 2),
                    })
                    trade_count += 1
                print(f"  {code} ({stock_type}): {trade_count} 笔交易")
            except Exception as e:
                print(f"  {code} ({stock_type}): 回测失败 - {e}")
    finally:
        client.close()

    if not all_trades:
        print("无交易数据！")
        return

    # 统计
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t["return_pct"] > 0]
    losses = [t for t in all_trades if t["return_pct"] <= 0]
    total_profit = sum(t["return_pct"] for t in wins)
    total_loss = abs(sum(t["return_pct"] for t in losses))
    avg_win = total_profit / len(wins) if wins else 0.0
    avg_loss = total_loss / len(losses) if losses else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0.0
    net_profit = total_profit - total_loss

    # 优化后结果
    print()
    print("=" * 70)
    print("优化后回测结果（盈利锁定阶梯）")
    print("=" * 70)
    print(f"总交易数: {total_trades}")
    print(f"胜率: {win_rate:.1f}% ({len(wins)}胜 {len(losses)}负)")
    print(f"总盈利: +{total_profit:.2f}%")
    print(f"总亏损: -{total_loss:.2f}%")
    print(f"净收益: {net_profit:+.2f}%")
    print(f"盈亏比(PF): {profit_factor:.2f}")
    print(f"平均盈利: +{avg_win:.2f}%")
    print(f"平均亏损: -{avg_loss:.2f}%")
    print()

    # 基线对比
    print("=" * 70)
    print("基线 vs 优化后 对比")
    print("=" * 70)
    b = BASELINE
    print(f"{'指标':<12} {'基线':>10} {'优化后':>10} {'变化':>10}")
    print("-" * 45)
    _print_row("交易数", b["trades"], total_trades, "d")
    _print_row("胜率%", b["win_rate"], win_rate, ".1f")
    _print_row("总盈利%", b["total_profit"], total_profit, ".2f")
    _print_row("总亏损%", b["total_loss"], total_loss, ".2f")
    _print_row("净收益%", b["net"], net_profit, ".2f")
    _print_row("盈亏比", b["pf"], profit_factor, ".2f")
    _print_row("平均盈利%", b["avg_win"], avg_win, ".2f")
    _print_row("平均亏损%", b["avg_loss"], avg_loss, ".2f")
    print()

    # 退出原因统计
    print("=" * 70)
    print("退出原因分布")
    print("=" * 70)
    reasons: dict[str, dict] = {}
    for trade in all_trades:
        r = trade["reason"] or "未知"
        if r not in reasons:
            reasons[r] = {"count": 0, "profit": 0.0, "wins": 0}
        reasons[r]["count"] += 1
        reasons[r]["profit"] += trade["return_pct"]
        if trade["return_pct"] > 0:
            reasons[r]["wins"] += 1
    for r, stats in sorted(reasons.items(), key=lambda x: -abs(x[1]["profit"])):
        wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
        print(f"  {r:<16} {stats['count']:>3}笔  胜率{wr:>5.1f}%  总收益{stats['profit']:>+8.2f}%")

    # 逐笔交易明细
    print()
    print("逐笔交易明细:")
    header = (
        f"{'代码':<8} {'类型':<8} {'买入日':<12} {'卖出日':<12} "
        f"{'买价':>8} {'卖价':>8} {'收益%':>8} {'退出原因':<16}"
    )
    print(header)
    print("-" * 90)
    for trade in all_trades:
        print(
            f"{trade['code']:<8} {trade['stock_type']:<8} "
            f"{trade['buy_date']:<12} {trade['sell_date']:<12} "
            f"{trade['buy_price']:>8.2f} {trade['sell_price']:>8.2f} "
            f"{trade['return_pct']:>+8.2f} {trade['reason']:<16}"
        )


if __name__ == "__main__":
    main()
