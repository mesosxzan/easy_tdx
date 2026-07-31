"""TRIX 优化策略参数扫描脚本。

对 chandelier_atr_mult（ATR 吊灯倍数）和 ma_fast（快线周期）进行网格搜索，
在 30 只 A 股上寻找最优参数组合。

扫描维度：
  - chandelier_atr_mult: [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]
  - ma_fast:            [5, 10, 15, 20]
  - ma_trend_period:    [60, 120]

共 8 × 4 × 2 = 64 组合 × 30 只股票 = 1920 次回测。

评分函数: composite_score = avg_return - 0.5 × avg_drawdown
（收益越高越好，回撤越低越好，权重 1:0.5）

用法::

    python strategies/eval_trix_param_sweep.py
    python strategies/eval_trix_param_sweep.py --top 20
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

# 触发内置策略注册
import easy_tdx.backtest.strategies.builtin  # noqa: E402, F401
from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategies import get_registry  # noqa: E402
from easy_tdx.cli.conn import get_mac_client  # noqa: E402
from easy_tdx.mac.enums import Adjust, Period  # noqa: E402
from easy_tdx.models.enums import Market  # noqa: E402

# ── 标的池（与 eval_trix_optimized.py 一致） ──────────────────────────────────

EVAL_STOCKS: list[tuple[str, str, str]] = [
    ("600519", "SH", "贵州茅台"),
    ("000858", "SZ", "五粮液"),
    ("000568", "SZ", "泸州老窖"),
    ("002304", "SZ", "洋河股份"),
    ("601318", "SH", "中国平安"),
    ("600036", "SH", "招商银行"),
    ("601166", "SH", "兴业银行"),
    ("000776", "SZ", "广发证券"),
    ("000725", "SZ", "京东方A"),
    ("002475", "SZ", "立讯精密"),
    ("300059", "SZ", "东方财富"),
    ("002405", "SZ", "四维图新"),
    ("600276", "SH", "恒瑞医药"),
    ("000538", "SZ", "云南白药"),
    ("300015", "SZ", "爱尔眼科"),
    ("300750", "SZ", "宁德时代"),
    ("002594", "SZ", "比亚迪"),
    ("601012", "SH", "隆基绿能"),
    ("601899", "SH", "紫金矿业"),
    ("600585", "SH", "海螺水泥"),
    ("601600", "SH", "中国铝业"),
    ("002407", "SZ", "多氟多"),
    ("300296", "SZ", "利亚德"),
    ("002236", "SZ", "大华股份"),
    ("601668", "SH", "中国建筑"),
    ("601111", "SH", "中国国航"),
    ("600028", "SH", "中国石化"),
    ("601666", "SH", "平煤股份"),
    ("600206", "SH", "有研新材"),
    ("000703", "SZ", "正虹科技"),
]

CASH = 1_000_000.0
COMMISSION = 0.0003
KLINE_COUNT = 800
WARMUP_BARS = 120

# ── 扫描网格 ──────────────────────────────────────────────────────────────────

CHANDELIER_ATR_MULTS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]
MA_FASTS = [5, 10, 15, 20]
MA_TRENDS = [60, 120]


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class ComboResult:
    """单组参数的汇总结果。"""

    chandelier_atr_mult: float
    ma_fast: int
    ma_trend_period: int

    avg_return: float = 0.0
    avg_drawdown: float = 0.0
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    avg_holding_days: float = 0.0
    total_trades: int = 0
    positive_count: int = 0
    beat_buyhold_count: int = 0
    valid_count: int = 0
    composite_score: float = 0.0

    # 逐股明细（仅存收益率和回撤，减少内存）
    stock_returns: list[float] = field(default_factory=list)
    stock_drawdowns: list[float] = field(default_factory=list)


def _market_from_code(market_str: str) -> Market:
    market_map = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
    return market_map.get(market_str, Market.SH)


def load_all_data() -> dict[str, Any]:
    """加载所有标的的 K 线数据，缓存到内存。"""
    data: dict[str, Any] = {}
    total = len(EVAL_STOCKS)

    print(f">> 加载数据 ({total} 只)...")
    with get_mac_client() as client:
        for idx, (symbol, market_str, name) in enumerate(EVAL_STOCKS, 1):
            code = f"{symbol}.{market_str}"
            print(f"  [{idx}/{total}] {name}({code})...", end=" ", flush=True)
            try:
                market = _market_from_code(market_str)
                df = client.get_stock_kline(
                    market,
                    symbol,
                    period=Period.DAILY,
                    start=0,
                    count=KLINE_COUNT,
                    adjust=Adjust.QFQ,
                )
                if df is not None and len(df) >= WARMUP_BARS + 20:
                    data[code] = df
                    print(f"OK ({len(df)} 根)")
                else:
                    print("SKIP (K线不足)")
            except Exception as exc:
                print(f"ERROR ({exc})")

    print(f"  成功加载 {len(data)}/{total} 只\n")
    return data


def run_combo(
    strategy_cls: type,
    params: dict[str, Any],
    data: dict[str, Any],
) -> ComboResult:
    """对一组参数运行所有标的的回测。"""
    cr = ComboResult(
        chandelier_atr_mult=params["chandelier_atr_mult"],
        ma_fast=params["ma_fast"],
        ma_trend_period=params["ma_trend_period"],
    )

    for symbol, market_str, name in EVAL_STOCKS:
        code = f"{symbol}.{market_str}"
        df = data.get(code)
        if df is None:
            continue

        try:
            strategy = strategy_cls(**params)
            engine = BacktestEngine(
                strategy=strategy,
                cash=CASH,
                commission=COMMISSION,
                warmup_bars=WARMUP_BARS,
            )
            result = engine.run(df)
            perf = result.performance

            ret = perf.get("total_return", 0.0)
            dd = perf.get("max_drawdown", 0.0)
            sharpe = perf.get("sharpe", 0.0)
            win_rate = perf.get("win_rate", 0.0)
            holding = perf.get("avg_holding_days", 0.0)
            trades = perf.get("total_trades", 0)

            closes = df["close"].values
            bh_ret = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0

            cr.stock_returns.append(ret)
            cr.stock_drawdowns.append(dd)
            cr.avg_return += ret
            cr.avg_drawdown += dd
            cr.avg_sharpe += sharpe
            cr.avg_win_rate += win_rate
            cr.avg_holding_days += holding
            cr.total_trades += trades
            cr.valid_count += 1
            if ret > 0:
                cr.positive_count += 1
            if ret > bh_ret:
                cr.beat_buyhold_count += 1

        except Exception:
            pass

    if cr.valid_count > 0:
        n = cr.valid_count
        cr.avg_return /= n
        cr.avg_drawdown /= n
        cr.avg_sharpe /= n
        cr.avg_win_rate /= n
        cr.avg_holding_days /= n

    # 复合评分: 收益 - 0.5 × 回撤（回撤越小越好所以减去）
    cr.composite_score = cr.avg_return - 0.5 * cr.avg_drawdown

    return cr


def run_baseline_orig(
    data: dict[str, Any],
) -> tuple[float, float, float, float, int, int, int]:
    """运行原版 TRIX 策略作为基线。"""
    registry = get_registry()
    orig_cls = registry.get("trix").strategy_cls

    returns: list[float] = []
    drawdowns: list[float] = []
    sharpes: list[float] = []
    win_rates: list[float] = []
    total_trades = 0
    positive = 0
    beat_bh = 0
    count = 0

    for symbol, market_str, name in EVAL_STOCKS:
        code = f"{symbol}.{market_str}"
        df = data.get(code)
        if df is None:
            continue

        try:
            engine = BacktestEngine(
                strategy=orig_cls,
                cash=CASH,
                commission=COMMISSION,
                warmup_bars=max(WARMUP_BARS, 60),
            )
            result = engine.run(df)
            perf = result.performance

            ret = perf.get("total_return", 0.0)
            dd = perf.get("max_drawdown", 0.0)

            closes = df["close"].values
            bh_ret = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0

            returns.append(ret)
            drawdowns.append(dd)
            sharpes.append(perf.get("sharpe", 0.0))
            win_rates.append(perf.get("win_rate", 0.0))
            total_trades += perf.get("total_trades", 0)
            if ret > 0:
                positive += 1
            if ret > bh_ret:
                beat_bh += 1
            count += 1
        except Exception:
            pass

    if count == 0:
        return 0.0, 0.0, 0.0, 0.0, 0, 0, 0

    return (
        sum(returns) / count,
        sum(drawdowns) / count,
        sum(sharpes) / count,
        sum(win_rates) / count,
        total_trades,
        positive,
        beat_bh,
    )


def print_results(
    baseline: tuple[float, float, float, float, int, int, int],
    combos: list[ComboResult],
    top: int,
) -> None:
    """打印扫描结果。"""
    orig_ret, orig_dd, orig_sharpe, orig_win, orig_trades, orig_pos, orig_beat = baseline

    print(f"\n{'=' * 140}")
    print("  ★ 参数扫描结果 ★")
    print(f"{'=' * 140}")

    # 基线
    print("\n  --- 基线: 原版 TRIX ---")
    print(f"  平均收益={orig_ret * 100:+.2f}%  平均回撤={orig_dd * 100:.2f}%  "
          f"夏普={orig_sharpe:.3f}  胜率={orig_win * 100:.1f}%  "
          f"正收益={orig_pos}/30  跑赢持有={orig_beat}/30  总交易={orig_trades}")

    # 按复合评分排序
    sorted_combos = sorted(combos, key=lambda c: c.composite_score, reverse=True)

    print(f"\n  --- Top {min(top, len(sorted_combos))} 参数组合（按复合评分排序）---")
    print("  评分 = avg_return - 0.5 × avg_drawdown")
    print()
    header = (
        f"  {'排名':>4} {'ATR倍数':>7} {'快线':>4} {'趋势MA':>6} "
        f"{'平均收益':>9} {'平均回撤':>9} {'夏普':>7} {'胜率':>7} "
        f"{'持仓天':>6} {'正收益':>7} {'跑赢持有':>8} {'交易':>6} {'评分':>8}"
    )
    print(header)
    print(f"  {'-' * 120}")

    for rank, cr in enumerate(sorted_combos[:top], 1):
        marker = " ← " if rank == 1 else "   "
        print(
            f"  {rank:>4} {cr.chandelier_atr_mult:>7.1f} {cr.ma_fast:>4} "
            f"{cr.ma_trend_period:>6} "
            f"{cr.avg_return * 100:>+8.2f}% {cr.avg_drawdown * 100:>8.2f}% "
            f"{cr.avg_sharpe:>7.3f} {cr.avg_win_rate * 100:>6.1f}% "
            f"{cr.avg_holding_days:>6.1f} "
            f"{cr.positive_count:>3}/{cr.valid_count:<3} "
            f"{cr.beat_buyhold_count:>4}/{cr.valid_count:<3} "
            f"{cr.total_trades:>6} "
            f"{cr.composite_score * 100:>+7.2f}{marker}"
        )

    # 与基线对比
    best = sorted_combos[0] if sorted_combos else None
    if best:
        print("\n  --- 最优组合 vs 原版 TRIX ---")
        print(f"  最优参数: ATR倍数={best.chandelier_atr_mult}  "
              f"快线=MA{best.ma_fast}  趋势MA={best.ma_trend_period}")
        print(f"  {'指标':<15} {'原版TRIX':>12} {'最优优化版':>12} {'差异':>12}")
        print(f"  {'-' * 55}")
        print(f"  {'平均收益率':<15} {orig_ret * 100:>+11.2f}% "
              f"{best.avg_return * 100:>+11.2f}% "
              f"{(best.avg_return - orig_ret) * 100:>+11.2f}pp")
        print(f"  {'平均回撤':<15} {orig_dd * 100:>11.2f}% "
              f"{best.avg_drawdown * 100:>11.2f}% "
              f"{(best.avg_drawdown - orig_dd) * 100:>+11.2f}pp")
        print(f"  {'夏普比率':<15} {orig_sharpe:>12.3f} "
              f"{best.avg_sharpe:>12.3f} "
              f"{best.avg_sharpe - orig_sharpe:>+12.3f}")
        print(f"  {'胜率':<15} {orig_win * 100:>11.1f}% "
              f"{best.avg_win_rate * 100:>11.1f}% "
              f"{(best.avg_win_rate - orig_win) * 100:>+11.1f}pp")
        print(f"  {'正收益比例':<15} {orig_pos:>7}/30     "
              f"{best.positive_count:>7}/{best.valid_count}     "
              f"{best.positive_count - orig_pos:>+12}")
        print(f"  {'跑赢持有':<15} {orig_beat:>7}/30     "
              f"{best.beat_buyhold_count:>7}/{best.valid_count}     "
              f"{best.beat_buyhold_count - orig_beat:>+12}")
        print(f"  {'总交易':<15} {orig_trades:>12} {best.total_trades:>12} "
              f"{best.total_trades - orig_trades:>+12}")

    # 按不同维度排序的 Top 3
    print("\n  --- 按不同维度排序的 Top 3 ---")

    for metric, label, fmt in [
        ("avg_return", "平均收益率", lambda c: c.avg_return),
        ("avg_drawdown", "最低回撤", lambda c: -c.avg_drawdown),
        ("avg_sharpe", "夏普比率", lambda c: c.avg_sharpe),
        ("composite_score", "复合评分", lambda c: c.composite_score),
    ]:
        print(f"\n  [{label}]")
        sorted_by = sorted(combos, key=fmt, reverse=True)
        for rank, cr in enumerate(sorted_by[:3], 1):
            print(
                f"    {rank}. ATR={cr.chandelier_atr_mult} MA{cr.ma_fast} "
                f"趋势MA{cr.ma_trend_period}  "
                f"收益={cr.avg_return * 100:+.2f}%  "
                f"回撤={cr.avg_drawdown * 100:.2f}%  "
                f"夏普={cr.avg_sharpe:.3f}  "
                f"评分={cr.composite_score * 100:+.2f}"
            )


def main(top: int = 15) -> None:
    print("=" * 140)
    print("  TRIX 优化策略参数扫描")
    print(f"  标的数: {len(EVAL_STOCKS)} 只 | K线: {KLINE_COUNT} 根 | "
          f"资金: {CASH:,.0f} 元 | 预热: {WARMUP_BARS} 根")
    print(f"  扫描维度: chandelier_atr_mult={CHANDELIER_ATR_MULTS}")
    print(f"            ma_fast={MA_FASTS}")
    print(f"            ma_trend_period={MA_TRENDS}")
    print(f"  总组合数: {len(CHANDELIER_ATR_MULTS) * len(MA_FASTS) * len(MA_TRENDS)}")
    total_bt = len(CHANDELIER_ATR_MULTS) * len(MA_FASTS) * len(MA_TRENDS) * len(EVAL_STOCKS)
    print(f"  总回测数: {total_bt}")
    print("  数据源: MAC 客户端（通达信 MAC 协议），前复权日线")
    print("=" * 140)

    # Step 1: 加载数据
    data = load_all_data()
    if not data:
        print("ERROR: 无可用数据")
        return

    # Step 2: 运行基线
    print(">> 运行基线 (原版 TRIX)...")
    t0 = time.time()
    baseline = run_baseline_orig(data)
    print(f"   基线完成 (耗时 {time.time() - t0:.1f}s)\n")

    # Step 3: 网格扫描
    registry = get_registry()
    opt_cls = registry.get("trix_cross_optimized").strategy_cls

    combos: list[ComboResult] = []
    total_combos = len(CHANDELIER_ATR_MULTS) * len(MA_FASTS) * len(MA_TRENDS)
    combo_idx = 0

    print(f">> 网格扫描 ({total_combos} 组合)...")
    t0 = time.time()

    for atr_mult, ma_fast, ma_trend in product(
        CHANDELIER_ATR_MULTS, MA_FASTS, MA_TRENDS
    ):
        combo_idx += 1
        params = {
            "chandelier_atr_mult": atr_mult,
            "ma_fast": ma_fast,
            "ma_trend_period": ma_trend,
        }
        cr = run_combo(opt_cls, params, data)
        combos.append(cr)

        elapsed = time.time() - t0
        eta = elapsed / combo_idx * (total_combos - combo_idx)
        print(
            f"  [{combo_idx}/{total_combos}] ATR={atr_mult} MA{ma_fast} "
            f"趋势MA{ma_trend}  "
            f"收益={cr.avg_return * 100:+.2f}%  "
            f"回撤={cr.avg_drawdown * 100:.2f}%  "
            f"评分={cr.composite_score * 100:+.2f}  "
            f"({elapsed:.0f}s / ETA {eta:.0f}s)",
            flush=True,
        )

    total_time = time.time() - t0
    print(f"\n  扫描完成 (耗时 {total_time:.1f}s, "
          f"平均 {total_time / total_combos:.2f}s/组合)")

    # Step 4: 打印结果
    print_results(baseline, combos, top)

    print(f"\n{'=' * 140}")
    print("  扫描完成")
    print(f"{'=' * 140}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="TRIX 优化策略参数扫描"
    )
    parser.add_argument(
        "--top", type=int, default=15,
        help="显示前 N 名结果 (默认 15)",
    )
    args = parser.parse_args()

    main(top=args.top)
