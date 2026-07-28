"""优化前后回测对比脚本：基线 vs 出场端优化。

在同一股票池上运行基线策略（shadow_yang_v2_baseline.py）和优化后策略
（shadow_yang_v2.py），逐笔对比交易结果，验证出场端优化的效果。

优化内容:
  #22 强趋势盈利锁定豁免：ADX>=30 多头强趋势时跳过 Tier1/Tier2，从 Tier3 开始
  #23 超大幅盈利追踪放宽：浮盈>25% 时追踪宽度从 2.0×ATR 放宽到 2.5×ATR

用法::

    python scripts/backtest_optimization_compare.py

输出:
  1. 每只股票的基线 vs 优化回测摘要
  2. 全量交易汇总对比（交易数/胜率/PF/净收益）
  3. 被过滤交易明细（基线有但优化后无的交易）
  4. 逐笔交易对比
"""

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
from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────────

BASELINE_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2_baseline.py"
OPTIMIZED_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"
KLINE_COUNT = 500
CASH = 100_000.0
COMMISSION = 0.0003

# 股票池：与因子分析脚本一致（50 只个股，5 组 wencai 随机样本）
STOCK_POOL: list[tuple[str, str]] = [
    ("600206", "SH"), ("002407", "SZ"), ("002156", "SZ"), ("600539", "SH"),
    ("000001", "SZ"), ("600519", "SH"), ("000858", "SZ"), ("600550", "SH"),
    ("600176", "SH"), ("600403", "SH"), ("300207", "SZ"),
    ("000725", "SZ"), ("601991", "SH"), ("600664", "SH"), ("000100", "SZ"),
    ("601678", "SH"), ("002185", "SZ"), ("600157", "SH"), ("601288", "SH"),
    ("600744", "SH"), ("002498", "SZ"),
    ("001301", "SZ"), ("600004", "SH"), ("301035", "SZ"), ("300199", "SZ"),
    ("002518", "SZ"), ("300821", "SZ"), ("603833", "SH"), ("002461", "SZ"),
    ("002402", "SZ"), ("600339", "SH"),
    ("603823", "SH"), ("920725", "BJ"), ("920699", "BJ"), ("603580", "SH"),
    ("301319", "SZ"), ("301520", "SZ"), ("603137", "SH"),
    ("002189", "SZ"), ("002853", "SZ"), ("600188", "SH"), ("603057", "SH"),
    ("002879", "SZ"), ("002270", "SZ"), ("600671", "SH"), ("002810", "SZ"),
    ("600729", "SH"), ("002265", "SZ"),
    ("920088", "BJ"), ("300164", "SZ"),
]


def _load_strategy_class(file_path: Path, module_name: str) -> type[Strategy]:
    """从文件加载 Strategy 子类。"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
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


def _run_backtest(
    client: MacClient,
    code: str,
    market_str: str,
    strategy_cls: type[Strategy],
) -> Any:
    """获取数据并运行回测，返回回测结果。"""
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
    return engine.run(df)


def _extract_trades(result: Any, code: str) -> list[dict]:
    """从回测结果中提取配对交易（买入->卖出）。"""
    trades_df = result.trades
    if trades_df.empty:
        return []

    all_trades: list[dict] = []
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
        buy_price = float(buy_row["price"])
        sell_price = float(sell_row["price"])
        cost_basis = float(sell_row.get("cost_basis", 0))
        pnl = float(sell_row.get("pnl", 0))
        if cost_basis > 0:
            return_pct = pnl / cost_basis * 100
        else:
            return_pct = (sell_price - buy_price) / buy_price * 100

        all_trades.append({
            "code": code,
            "buy_date": str(buy_row["datetime"]),
            "sell_date": str(sell_row["datetime"]),
            "buy_price": round(buy_price, 2),
            "sell_price": round(sell_price, 2),
            "return_pct": round(return_pct, 2),
            "is_win": return_pct > 0,
            "reason": str(sell_row.get("reason", "")),
            "buy_reason": str(buy_row.get("reason", "")),
        })

    return all_trades


def _calc_stats(trades: list[dict]) -> dict[str, float | int]:
    """计算交易统计。"""
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_profit": 0.0, "total_loss": 0.0, "net": 0.0,
            "pf": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }
    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]
    total_profit = sum(t["return_pct"] for t in wins)
    total_loss = abs(sum(t["return_pct"] for t in losses))
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "total_profit": total_profit,
        "total_loss": total_loss,
        "net": total_profit - total_loss,
        "pf": total_profit / total_loss if total_loss > 0 else float("inf"),
        "avg_win": total_profit / len(wins) if wins else 0.0,
        "avg_loss": total_loss / len(losses) if losses else 0.0,
    }


def _print_comparison_row(label: str, baseline: float, optimized: float, fmt: str) -> None:
    """打印一行对比数据。"""
    diff = optimized - baseline
    arrow = "↑" if diff > 0 else "↓" if diff < 0 else "="
    if fmt == "d":
        print(f"  {label:<14} {int(baseline):>10} {int(optimized):>10} {arrow} {int(diff):>+8}")
    else:
        print(f"  {label:<14} {baseline:>10{fmt}} {optimized:>10{fmt}} {arrow} {diff:>+8{fmt}}")


def main() -> None:
    print("=" * 90)
    print("优化前后回测对比：出场端优化（强趋势盈利锁定豁免 + 超大幅追踪放宽）")
    print(f"基线: {BASELINE_FILE.name} | 优化: {OPTIMIZED_FILE.name}")
    print(f"K线: {KLINE_COUNT} | 股票: {len(STOCK_POOL)} 只 | 资金: {CASH:,.0f}")
    print("=" * 90)

    # 加载两个策略版本
    baseline_cls = _load_strategy_class(BASELINE_FILE, "baseline_strategy")
    optimized_cls = _load_strategy_class(OPTIMIZED_FILE, "optimized_strategy")
    print(f"基线策略类: {baseline_cls.__name__}")
    print(f"优化策略类: {optimized_cls.__name__}")

    # 连接数据源
    print("\n正在连接行情服务器...")
    client = MacClient.from_best_host()
    client.connect()

    baseline_all_trades: list[dict] = []
    optimized_all_trades: list[dict] = []
    per_stock_stats: list[dict] = []

    try:
        print(f"\n{'=' * 90}")
        print("逐股回测结果")
        print(f"{'=' * 90}")
        print(
            f"  {'股票':<8} {'基线交易':>8} {'优化交易':>8} {'基线胜率':>8} "
            f"{'优化胜率':>8} {'基线净收益':>10} {'优化净收益':>10}"
        )
        print("  " + "-" * 80)

        for code, market_str in STOCK_POOL:
            try:
                # 运行基线
                result_base = _run_backtest(client, code, market_str, baseline_cls)
                base_trades = _extract_trades(result_base, code)

                # 运行优化
                result_opt = _run_backtest(client, code, market_str, optimized_cls)
                opt_trades = _extract_trades(result_opt, code)

                base_stats = _calc_stats(base_trades)
                opt_stats = _calc_stats(opt_trades)

                baseline_all_trades.extend(base_trades)
                optimized_all_trades.extend(opt_trades)

                per_stock_stats.append({
                    "code": code,
                    "base_trades": base_stats["trades"],
                    "opt_trades": opt_stats["trades"],
                    "base_win_rate": base_stats["win_rate"],
                    "opt_win_rate": opt_stats["win_rate"],
                    "base_net": base_stats["net"],
                    "opt_net": opt_stats["net"],
                })

                print(
                    f"  {code:<8} {base_stats['trades']:>8} {opt_stats['trades']:>8} "
                    f"{base_stats['win_rate']:>7.1f}% {opt_stats['win_rate']:>7.1f}% "
                    f"{base_stats['net']:>+9.2f}% {opt_stats['net']:>+9.2f}%"
                )
            except Exception as e:
                print(f"  {code:<8} 错误: {e}")
    finally:
        client.close()

    if not baseline_all_trades and not optimized_all_trades:
        print("\n[!] 无交易数据！")
        return

    # 全量统计对比
    base_stats = _calc_stats(baseline_all_trades)
    opt_stats = _calc_stats(optimized_all_trades)

    print(f"\n{'=' * 90}")
    print("全量交易汇总对比")
    print(f"{'=' * 90}")
    print(f"  {'指标':<14} {'基线':>10} {'优化后':>10} {'变化':>10}")
    print("  " + "-" * 50)
    _print_comparison_row("交易数", base_stats["trades"], opt_stats["trades"], "d")
    _print_comparison_row("盈利数", base_stats["wins"], opt_stats["wins"], "d")
    _print_comparison_row("亏损数", base_stats["losses"], opt_stats["losses"], "d")
    _print_comparison_row("胜率%", base_stats["win_rate"], opt_stats["win_rate"], ".1f")
    _print_comparison_row("总盈利%", base_stats["total_profit"], opt_stats["total_profit"], ".2f")
    _print_comparison_row("总亏损%", base_stats["total_loss"], opt_stats["total_loss"], ".2f")
    _print_comparison_row("净收益%", base_stats["net"], opt_stats["net"], ".2f")
    _print_comparison_row("盈亏比(PF)", base_stats["pf"], opt_stats["pf"], ".2f")
    _print_comparison_row("平均盈利%", base_stats["avg_win"], opt_stats["avg_win"], ".2f")
    _print_comparison_row("平均亏损%", base_stats["avg_loss"], opt_stats["avg_loss"], ".2f")

    # 被过滤的交易（基线有但优化后无）
    print(f"\n{'=' * 90}")
    print("被过滤交易明细（基线有但优化后无）")
    print(f"{'=' * 90}")

    # 构建优化后的交易 key 集合（code + buy_date）
    opt_keys = {f"{t['code']}_{t['buy_date']}" for t in optimized_all_trades}
    filtered_trades = [
        t for t in baseline_all_trades
        if f"{t['code']}_{t['buy_date']}" not in opt_keys
    ]

    if filtered_trades:
        filt_wins = [t for t in filtered_trades if t["is_win"]]
        filt_losses = [t for t in filtered_trades if not t["is_win"]]
        filt_profit = sum(t["return_pct"] for t in filt_wins)
        filt_loss = abs(sum(t["return_pct"] for t in filt_losses))
        n_filt = len(filtered_trades)
        print(f"  共 {n_filt} 笔被过滤（{len(filt_wins)} 盈利 + {len(filt_losses)} 亏损）")
        print(f"  被过滤盈利总额: +{filt_profit:.2f}%")
        print(f"  被过滤亏损总额: -{filt_loss:.2f}%")
        net_effect = filt_profit - filt_loss
        effect_label = "减少亏损" if filt_loss > filt_profit else "减少盈利"
        print(f"  净效果: {net_effect:+.2f}%（{effect_label}）")
        print()
        print(
            f"  {'股票':<8} {'买入日':<12} {'买入价':>8} {'卖出价':>8} {'收益%':>8} "
            f"{'胜':>4} {'买入原因':<14} {'卖出原因':<16}"
        )
        print("  " + "-" * 90)
        for t in sorted(filtered_trades, key=lambda x: x["return_pct"]):
            win_mark = "✓" if t["is_win"] else "✗"
            print(
                f"  {t['code']:<8} {t['buy_date']:<12} {t['buy_price']:>8.2f} "
                f"{t['sell_price']:>8.2f} {t['return_pct']:>+8.2f} {win_mark:>4} "
                f"{t['buy_reason']:<14} {t['reason']:<16}"
            )
    else:
        print("  无被过滤交易")

    # 新增交易（优化后有但基线无）
    print(f"\n{'=' * 90}")
    print("新增交易明细（优化后有但基线无）")
    print(f"{'=' * 90}")
    base_keys = {f"{t['code']}_{t['buy_date']}" for t in baseline_all_trades}
    new_trades = [
        t for t in optimized_all_trades
        if f"{t['code']}_{t['buy_date']}" not in base_keys
    ]
    if new_trades:
        for t in sorted(new_trades, key=lambda x: x["return_pct"]):
            win_mark = "✓" if t["is_win"] else "✗"
            print(
                f"  {t['code']:<8} {t['buy_date']:<12} {t['buy_price']:>8.2f} "
                f"{t['sell_price']:>8.2f} {t['return_pct']:>+8.2f} {win_mark:>4} "
                f"{t['buy_reason']:<14} {t['reason']:<16}"
            )
    else:
        print("  无新增交易")

    # 退出原因分布对比
    print(f"\n{'=' * 90}")
    print("退出原因分布对比")
    print(f"{'=' * 90}")
    for label, trades_list in [("基线", baseline_all_trades), ("优化后", optimized_all_trades)]:
        reasons: dict[str, dict] = {}
        for trade in trades_list:
            r = trade["reason"] or "未知"
            if r not in reasons:
                reasons[r] = {"count": 0, "profit": 0.0, "wins": 0}
            reasons[r]["count"] += 1
            reasons[r]["profit"] += trade["return_pct"]
            if trade["is_win"]:
                reasons[r]["wins"] += 1
        print(f"\n  [{label}]")
        for r, stats in sorted(reasons.items(), key=lambda x: -abs(x[1]["profit"])):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            print(
                f"    {r:<16} {stats['count']:>3}笔  胜率{wr:>5.1f}%  "
                f"总收益{stats['profit']:>+8.2f}%"
            )

    # 买入信号类型对比
    print(f"\n{'=' * 90}")
    print("买入信号类型对比")
    print(f"{'=' * 90}")
    for label, trades_list in [("基线", baseline_all_trades), ("优化后", optimized_all_trades)]:
        reasons: dict[str, dict] = {}
        for trade in trades_list:
            r = trade["buy_reason"] or "未知"
            if r not in reasons:
                reasons[r] = {"count": 0, "profit": 0.0, "wins": 0}
            reasons[r]["count"] += 1
            reasons[r]["profit"] += trade["return_pct"]
            if trade["is_win"]:
                reasons[r]["wins"] += 1
        print(f"\n  [{label}]")
        for r, stats in sorted(reasons.items(), key=lambda x: -x[1]["count"]):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            print(
                f"    {r:<16} {stats['count']:>3}笔  胜率{wr:>5.1f}%  "
                f"总收益{stats['profit']:>+8.2f}%"
            )

    print(f"\n{'=' * 90}")
    print("对比完成")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
