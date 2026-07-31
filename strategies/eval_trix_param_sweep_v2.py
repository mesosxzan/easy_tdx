"""TRIX 优化策略参数扫描 V2 -- 精细搜索。

基于 V1 扫描结果，在最优区域附近进行更精细的网格搜索。

V1 发现：
  - MA15 是最佳快线（MA5 太敏感，MA20 退化）
  - ATR 倍数越高收益越好（6.0 > 5.0 > 3.5）
  - MA60 趋势过滤优于 MA120

V2 新增维度：
  - ma_mid: [10, 15, 20, 30]（当 ma_mid=ma_fast 时，快线止盈波动率过滤恒 false，
    相当于禁用快线止盈，仅靠 ATR 吊灯止损）
  - 更高 ATR 倍数: [4.0, 5.0, 6.0, 8.0, 10.0, 99.0]（99=禁用吊灯止损）

用法::

    python strategies/eval_trix_param_sweep_v2.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

import easy_tdx.backtest.strategies.builtin  # noqa: E402, F401
from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategies import get_registry  # noqa: E402
from easy_tdx.cli.conn import get_mac_client  # noqa: E402
from easy_tdx.mac.enums import Adjust, Period  # noqa: E402
from easy_tdx.models.enums import Market  # noqa: E402

EVAL_STOCKS: list[tuple[str, str, str]] = [
    ("600519", "SH", "贵州茅台"), ("000858", "SZ", "五粮液"),
    ("000568", "SZ", "泸州老窖"), ("002304", "SZ", "洋河股份"),
    ("601318", "SH", "中国平安"), ("600036", "SH", "招商银行"),
    ("601166", "SH", "兴业银行"), ("000776", "SZ", "广发证券"),
    ("000725", "SZ", "京东方A"), ("002475", "SZ", "立讯精密"),
    ("300059", "SZ", "东方财富"), ("002405", "SZ", "四维图新"),
    ("600276", "SH", "恒瑞医药"), ("000538", "SZ", "云南白药"),
    ("300015", "SZ", "爱尔眼科"), ("300750", "SZ", "宁德时代"),
    ("002594", "SZ", "比亚迪"), ("601012", "SH", "隆基绿能"),
    ("601899", "SH", "紫金矿业"), ("600585", "SH", "海螺水泥"),
    ("601600", "SH", "中国铝业"), ("002407", "SZ", "多氟多"),
    ("300296", "SZ", "利亚德"), ("002236", "SZ", "大华股份"),
    ("601668", "SH", "中国建筑"), ("601111", "SH", "中国国航"),
    ("600028", "SH", "中国石化"), ("601666", "SH", "平煤股份"),
    ("600206", "SH", "有研新材"), ("000703", "SZ", "正虹科技"),
]

CASH = 1_000_000.0
COMMISSION = 0.0003
KLINE_COUNT = 800
WARMUP_BARS = 120

# V2 精细网格
CHANDELIER_ATR_MULTS = [4.0, 5.0, 6.0, 8.0, 10.0, 99.0]
MA_FASTS = [10, 15]
MA_MIDS = [10, 15, 20, 30]
MA_TRENDS = [60]


@dataclass
class ComboResult:
    chandelier_atr_mult: float
    ma_fast: int
    ma_mid: int
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
    median_return: float = 0.0


def _market_from_code(market_str: str) -> Market:
    return {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}.get(market_str, Market.SH)


def load_all_data() -> dict[str, Any]:
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
                    market, symbol, period=Period.DAILY, start=0,
                    count=KLINE_COUNT, adjust=Adjust.QFQ,
                )
                if df is not None and len(df) >= WARMUP_BARS + 20:
                    data[code] = df
                    print(f"OK ({len(df)} 根)")
                else:
                    print("SKIP")
            except Exception as exc:
                print(f"ERROR ({exc})")
    print(f"  成功加载 {len(data)}/{total} 只\n")
    return data


def run_combo(
    strategy_cls: type,
    params: dict[str, Any],
    data: dict[str, Any],
) -> ComboResult:
    cr = ComboResult(
        chandelier_atr_mult=params["chandelier_atr_mult"],
        ma_fast=params["ma_fast"],
        ma_mid=params["ma_mid"],
        ma_trend_period=params["ma_trend_period"],
    )
    returns: list[float] = []

    for symbol, market_str, name in EVAL_STOCKS:
        code = f"{symbol}.{market_str}"
        df = data.get(code)
        if df is None:
            continue
        try:
            strategy = strategy_cls(**params)
            engine = BacktestEngine(
                strategy=strategy, cash=CASH,
                commission=COMMISSION, warmup_bars=WARMUP_BARS,
            )
            result = engine.run(df)
            perf = result.performance
            ret = perf.get("total_return", 0.0)
            closes = df["close"].values
            bh_ret = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0

            returns.append(ret)
            cr.avg_return += ret
            cr.avg_drawdown += perf.get("max_drawdown", 0.0)
            cr.avg_sharpe += perf.get("sharpe", 0.0)
            cr.avg_win_rate += perf.get("win_rate", 0.0)
            cr.avg_holding_days += perf.get("avg_holding_days", 0.0)
            cr.total_trades += perf.get("total_trades", 0)
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
        returns.sort()
        mid = len(returns) // 2
        cr.median_return = returns[mid] if returns else 0.0

    # 复合评分: 收益 - 0.5 × 回撤
    cr.composite_score = cr.avg_return - 0.5 * cr.avg_drawdown
    return cr


def run_baseline(data: dict[str, Any]) -> tuple[float, float, float, float, int, int, int, float]:
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
                strategy=orig_cls, cash=CASH,
                commission=COMMISSION, warmup_bars=max(WARMUP_BARS, 60),
            )
            result = engine.run(df)
            perf = result.performance
            ret = perf.get("total_return", 0.0)
            closes = df["close"].values
            bh_ret = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0
            returns.append(ret)
            drawdowns.append(perf.get("max_drawdown", 0.0))
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
        return 0, 0, 0, 0, 0, 0, 0, 0
    returns.sort()
    return (
        sum(returns) / count,
        sum(drawdowns) / count,
        sum(sharpes) / count,
        sum(win_rates) / count,
        total_trades, positive, beat_bh,
        returns[count // 2],
    )


def main(top: int = 20) -> None:
    total_combos = len(CHANDELIER_ATR_MULTS) * len(MA_FASTS) * len(MA_MIDS) * len(MA_TRENDS)
    print("=" * 140)
    print("  TRIX 优化策略参数扫描 V2 -- 精细搜索")
    print(f"  标的数: {len(EVAL_STOCKS)} 只 | K线: {KLINE_COUNT} 根")
    print(f"  扫描维度: atr_mult={CHANDELIER_ATR_MULTS}")
    print(f"            ma_fast={MA_FASTS}")
    print(f"            ma_mid={MA_MIDS}")
    print(f"            ma_trend={MA_TRENDS}")
    print(f"  总组合数: {total_combos}")
    print(f"  总回测数: {total_combos * len(EVAL_STOCKS)}")
    print("=" * 140)

    data = load_all_data()
    if not data:
        print("ERROR: 无可用数据")
        return

    print(">> 运行基线 (原版 TRIX)...")
    t0 = time.time()
    baseline = run_baseline(data)
    print(f"   基线完成 (耗时 {time.time() - t0:.1f}s)\n")

    orig_ret, orig_dd, orig_sharpe, orig_win, orig_trades, orig_pos, orig_beat, orig_med = baseline

    registry = get_registry()
    opt_cls = registry.get("trix_cross_optimized").strategy_cls

    combos: list[ComboResult] = []
    combo_idx = 0
    print(f">> 网格扫描 ({total_combos} 组合)...")
    t0 = time.time()

    for atr_mult, ma_fast, ma_mid, ma_trend in product(
        CHANDELIER_ATR_MULTS, MA_FASTS, MA_MIDS, MA_TRENDS
    ):
        combo_idx += 1
        params = {
            "chandelier_atr_mult": atr_mult,
            "ma_fast": ma_fast,
            "ma_mid": ma_mid,
            "ma_trend_period": ma_trend,
        }
        cr = run_combo(opt_cls, params, data)
        combos.append(cr)
        elapsed = time.time() - t0
        eta = elapsed / combo_idx * (total_combos - combo_idx)
        exit_note = "纯ATR" if ma_mid == ma_fast else "ATR+快线"
        atr_note = "无" if atr_mult >= 99 else f"{atr_mult}"
        print(
            f"  [{combo_idx}/{total_combos}] ATR={atr_note:>4} MA{ma_fast} "
            f"mid{ma_mid} 趋势MA{ma_trend}  "
            f"收益={cr.avg_return * 100:+.2f}% 中位={cr.median_return * 100:+.2f}% "
            f"回撤={cr.avg_drawdown * 100:.2f}% "
            f"交易={cr.total_trades} 持仓={cr.avg_holding_days:.1f}d "
            f"评分={cr.composite_score * 100:+.2f} [{exit_note}]"
            f"  ({elapsed:.0f}s/ETA {eta:.0f}s)",
            flush=True,
        )

    total_time = time.time() - t0
    print(f"\n  扫描完成 (耗时 {total_time:.1f}s)\n")

    # ── 打印结果 ──
    print(f"{'=' * 150}")
    print("  ★ V2 参数扫描结果 ★")
    print(f"{'=' * 150}")

    print("\n  --- 基线: 原版 TRIX ---")
    print(f"  平均收益={orig_ret * 100:+.2f}%  中位收益={orig_med * 100:+.2f}%  "
          f"平均回撤={orig_dd * 100:.2f}%  夏普={orig_sharpe:.3f}  "
          f"胜率={orig_win * 100:.1f}%  正收益={orig_pos}/30  "
          f"跑赢持有={orig_beat}/30  总交易={orig_trades}")

    sorted_combos = sorted(combos, key=lambda c: c.composite_score, reverse=True)

    print(f"\n  --- Top {min(top, len(sorted_combos))} 参数组合（按复合评分排序）---")
    print("  评分 = avg_return - 0.5 × avg_drawdown\n")
    header = (
        f"  {'排名':>4} {'ATR':>5} {'快线':>4} {'中线':>4} {'趋势':>4} "
        f"{'均收益':>9} {'中位收益':>9} {'回撤':>8} {'夏普':>7} {'胜率':>7} "
        f"{'持仓天':>6} {'正收益':>7} {'跑赢':>6} {'交易':>5} {'评分':>8} {'模式':>10}"
    )
    print(header)
    print(f"  {'-' * 145}")

    for rank, cr in enumerate(sorted_combos[:top], 1):
        marker = " ← " if rank == 1 else "   "
        atr_label = "无" if cr.chandelier_atr_mult >= 99 else f"{cr.chandelier_atr_mult:.1f}"
        exit_mode = "纯ATR" if cr.ma_mid == cr.ma_fast else "ATR+快线"
        if cr.chandelier_atr_mult >= 99 and cr.ma_mid == cr.ma_fast:
            exit_mode = "纯快线"
        elif cr.chandelier_atr_mult >= 99:
            exit_mode = "纯快线+过滤"

        print(
            f"  {rank:>4} {atr_label:>5} MA{cr.ma_fast:>2} MA{cr.ma_mid:>2} "
            f"MA{cr.ma_trend_period:>3} "
            f"{cr.avg_return * 100:>+8.2f}% {cr.median_return * 100:>+8.2f}% "
            f"{cr.avg_drawdown * 100:>7.2f}% {cr.avg_sharpe:>7.3f} "
            f"{cr.avg_win_rate * 100:>6.1f}% {cr.avg_holding_days:>6.1f} "
            f"{cr.positive_count:>3}/{cr.valid_count:<3} "
            f"{cr.beat_buyhold_count:>3}/{cr.valid_count:<3} "
            f"{cr.total_trades:>5} "
            f"{cr.composite_score * 100:>+7.2f} {exit_mode:>10}{marker}"
        )

    # ── 按维度分析 ──
    print("\n  --- 按维度分析 ---")

    # ATR 倍数影响（固定 ma_fast=15, ma_mid=20, ma_trend=60）
    print("\n  [ATR 倍数影响] (固定 MA15, mid20, 趋势MA60)")
    for atr in CHANDELIER_ATR_MULTS:
        matching = [
            c for c in combos
            if c.chandelier_atr_mult == atr and c.ma_fast == 15
            and c.ma_mid == 20 and c.ma_trend_period == 60
        ]
        if matching:
            c = matching[0]
            atr_label = "无" if atr >= 99 else f"{atr:.1f}"
            print(f"    ATR={atr_label:>4}: 收益={c.avg_return * 100:+.2f}%  "
                  f"回撤={c.avg_drawdown * 100:.2f}%  交易={c.total_trades}  "
                  f"持仓={c.avg_holding_days:.1f}d  评分={c.composite_score * 100:+.2f}")

    # ma_mid 影响（固定 atr=6.0, ma_fast=15, ma_trend=60）
    print("\n  [ma_mid 影响] (固定 ATR=6.0, MA15, 趋势MA60)")
    for mid in MA_MIDS:
        matching = [
            c for c in combos
            if c.chandelier_atr_mult == 6.0 and c.ma_fast == 15
            and c.ma_mid == mid and c.ma_trend_period == 60
        ]
        if matching:
            c = matching[0]
            exit_note = "纯ATR(无快线)" if mid == 15 else f"ATR+快线(MA{mid})"
            print(f"    mid={mid:>2}: 收益={c.avg_return * 100:+.2f}%  "
                  f"回撤={c.avg_drawdown * 100:.2f}%  交易={c.total_trades}  "
                  f"持仓={c.avg_holding_days:.1f}d  "
                  f"评分={c.composite_score * 100:+.2f}  [{exit_note}]")

    # 最优 vs 基线
    best = sorted_combos[0] if sorted_combos else None
    if best:
        print("\n  --- 最优组合 vs 原版 TRIX ---")
        atr_label = "无" if best.chandelier_atr_mult >= 99 else f"{best.chandelier_atr_mult}"
        print(f"  最优参数: ATR倍数={atr_label}  快线=MA{best.ma_fast}  "
              f"中线=MA{best.ma_mid}  趋势MA={best.ma_trend_period}")
        print(f"  {'指标':<15} {'原版TRIX':>12} {'最优优化版':>12} {'差异':>12}")
        print(f"  {'-' * 55}")
        print(f"  {'平均收益率':<15} {orig_ret * 100:>+11.2f}% "
              f"{best.avg_return * 100:>+11.2f}% "
              f"{(best.avg_return - orig_ret) * 100:>+11.2f}pp")
        print(f"  {'中位收益率':<15} {orig_med * 100:>+11.2f}% "
              f"{best.median_return * 100:>+11.2f}% "
              f"{(best.median_return - orig_med) * 100:>+11.2f}pp")
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

    print(f"\n{'=' * 150}")
    print("  V2 扫描完成")
    print(f"{'=' * 150}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TRIX 优化策略参数扫描 V2")
    parser.add_argument("--top", type=int, default=20, help="显示前 N 名 (默认 20)")
    args = parser.parse_args()
    main(top=args.top)
