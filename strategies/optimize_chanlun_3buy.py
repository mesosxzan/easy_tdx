"""缠论三类买点策略参数优化脚本。

两阶段优化：
  Phase 1 — 逐个测试各退出条件，找到每个参数的最优值
  Phase 2 — 组合 Phase 1 的最优参数，做小规模网格搜索

用法::

    python strategies/optimize_chanlun_3buy.py
    python strategies/optimize_chanlun_3buy.py --stocks 600519 000858 --count 800
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保项目 src 在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from easy_tdx.cli.conn import get_mac_client
from easy_tdx.mac.enums import Period, Adjust
from easy_tdx.models.enums import Market
from easy_tdx.backtest.engine import BacktestEngine
from strategies.chanlun_3buy import Chanlun3BuyStrategy

# ── 白酒股票池 ────────────────────────────────────────────────────────────────

DEFAULT_STOCKS: list[tuple[str, Market, str]] = [
    ("600519", Market.SH, "贵州茅台"),
    ("000858", Market.SZ, "五粮液"),
    ("000568", Market.SZ, "泸州老窖"),
    ("600779", Market.SH, "水井坊"),
    ("000596", Market.SZ, "古井贡酒"),
    ("002304", Market.SZ, "洋河股份"),
    ("000799", Market.SZ, "酒鬼酒"),
    ("603369", Market.SH, "今世缘"),
    ("600809", Market.SH, "山西汾酒"),
]

# ── 参数定义 ──────────────────────────────────────────────────────────────────

PARAM_KEYS = [
    "stop_loss_pct",
    "take_profit_pct",
    "ma_exit_period",
    "trailing_stop_pct",
    "max_hold_days",
    "use_chanlun_sell",
]

BASELINE: dict[str, Any] = {
    "stop_loss_pct": 0,
    "take_profit_pct": 0,
    "ma_exit_period": 0,
    "trailing_stop_pct": 0,
    "max_hold_days": 0,
    "use_chanlun_sell": True,
}

# Phase 1: 逐个参数扫描（其余保持 baseline）
PHASE1_PARAMS: list[dict[str, Any]] = [
    # baseline（仅缠论卖点）
    {**BASELINE},
    # stop_loss 扫描
    {**BASELINE, "stop_loss_pct": 3},
    {**BASELINE, "stop_loss_pct": 5},
    {**BASELINE, "stop_loss_pct": 8},
    {**BASELINE, "stop_loss_pct": 10},
    {**BASELINE, "stop_loss_pct": 15},
    # take_profit 扫描
    {**BASELINE, "take_profit_pct": 10},
    {**BASELINE, "take_profit_pct": 15},
    {**BASELINE, "take_profit_pct": 20},
    {**BASELINE, "take_profit_pct": 30},
    {**BASELINE, "take_profit_pct": 50},
    # trailing_stop 扫描
    {**BASELINE, "trailing_stop_pct": 3},
    {**BASELINE, "trailing_stop_pct": 5},
    {**BASELINE, "trailing_stop_pct": 8},
    {**BASELINE, "trailing_stop_pct": 10},
    {**BASELINE, "trailing_stop_pct": 15},
    # ma_exit 扫描
    {**BASELINE, "ma_exit_period": 3},
    {**BASELINE, "ma_exit_period": 5},
    {**BASELINE, "ma_exit_period": 10},
    {**BASELINE, "ma_exit_period": 20},
    # max_hold 扫描
    {**BASELINE, "max_hold_days": 10},
    {**BASELINE, "max_hold_days": 20},
    {**BASELINE, "max_hold_days": 30},
    {**BASELINE, "max_hold_days": 40},
    # 无缠论卖点（纯被动退出）
    {**BASELINE, "use_chanlun_sell": False, "trailing_stop_pct": 10},
    {**BASELINE, "use_chanlun_sell": False, "max_hold_days": 20},
]


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    """单次回测的汇总指标。"""

    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0


@dataclass
class ParamResult:
    """一组参数在所有股票上的汇总结果。"""

    params: dict[str, Any]
    metrics: dict[str, BacktestMetrics] = field(default_factory=dict)
    avg_return: float = 0.0
    avg_drawdown: float = 0.0
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    avg_trades: float = 0.0

    def compute_avgs(self) -> None:
        """计算所有股票的平均指标。"""
        if not self.metrics:
            return
        n = len(self.metrics)
        self.avg_return = sum(m.total_return for m in self.metrics.values()) / n
        self.avg_drawdown = sum(m.max_drawdown for m in self.metrics.values()) / n
        self.avg_sharpe = sum(m.sharpe_ratio for m in self.metrics.values()) / n
        self.avg_win_rate = sum(m.win_rate for m in self.metrics.values()) / n
        self.avg_trades = sum(m.trade_count for m in self.metrics.values()) / n


# ── 核心函数 ──────────────────────────────────────────────────────────────────

def fetch_stock_data(
    client: Any,
    stocks: list[tuple[str, Market, str]],
    count: int = 800,
) -> dict[str, tuple[Any, str]]:
    """批量获取股票 K 线数据。

    Returns:
        {code: (DataFrame, name)}
    """
    data: dict[str, tuple[Any, str]] = {}
    for code, market, name in stocks:
        print(f"  获取 {name}({code}) ...", end=" ", flush=True)
        try:
            df = client.get_stock_kline(
                market,
                code,
                period=Period.DAILY,
                start=0,
                count=count,
                adjust=Adjust.QFQ,
            )
            if df is not None and len(df) > 0:
                data[code] = (df, name)
                print(f"OK ({len(df)} bars)")
            else:
                print("SKIP (no data)")
        except Exception as exc:
            print(f"FAIL ({exc})")
    return data


def apply_params(params: dict[str, Any]) -> None:
    """将参数应用到策略类属性。"""
    Chanlun3BuyStrategy.stop_loss_pct = params.get("stop_loss_pct", 0)
    Chanlun3BuyStrategy.take_profit_pct = params.get("take_profit_pct", 0)
    Chanlun3BuyStrategy.ma_exit_period = params.get("ma_exit_period", 0)
    Chanlun3BuyStrategy.trailing_stop_pct = params.get("trailing_stop_pct", 0)
    Chanlun3BuyStrategy.max_hold_days = params.get("max_hold_days", 0)
    Chanlun3BuyStrategy.use_chanlun_sell = params.get("use_chanlun_sell", True)


def run_backtest(
    df: Any,
    cash: float = 1_000_000,
    warmup_bars: int = 60,
) -> BacktestMetrics:
    """对单只股票运行回测，返回汇总指标。"""
    engine = BacktestEngine(
        strategy=Chanlun3BuyStrategy,
        cash=cash,
        chanlun_level="DAILY",
        warmup_bars=warmup_bars,
    )
    result = engine.run(df)
    perf = result.performance
    return BacktestMetrics(
        total_return=perf.get("total_return", 0.0),
        max_drawdown=perf.get("max_drawdown", 0.0),
        sharpe_ratio=perf.get("sharpe_ratio", 0.0),
        win_rate=perf.get("win_rate", 0.0),
        trade_count=len(result.trades),
    )


def run_phase(
    phase_name: str,
    param_list: list[dict[str, Any]],
    stock_data: dict[str, tuple[Any, str]],
    cash: float = 1_000_000,
    warmup_bars: int = 60,
) -> list[ParamResult]:
    """运行一个阶段的全部参数组合。"""
    results: list[ParamResult] = []
    total = len(param_list)

    for idx, params in enumerate(param_list, 1):
        label = _format_params(params)
        print(f"\n[{phase_name} {idx}/{total}] {label}")

        apply_params(params)
        pr = ParamResult(params=params)

        for code, (df, name) in stock_data.items():
            try:
                metrics = run_backtest(df, cash=cash, warmup_bars=warmup_bars)
                pr.metrics[code] = metrics
                ret_pct = metrics.total_return * 100
                print(f"    {name:6s} ret={ret_pct:+7.2f}%  trades={metrics.trade_count:3d}")
            except Exception as exc:
                print(f"    {name:6s} ERROR: {exc}")
                pr.metrics[code] = BacktestMetrics()

        pr.compute_avgs()
        results.append(pr)
        print(
            f"  => AVG ret={pr.avg_return * 100:+.2f}%  "
            f"dd={pr.avg_drawdown * 100:.2f}%  "
            f"sharpe={pr.avg_sharpe:.3f}  "
            f"win={pr.avg_win_rate * 100:.1f}%  "
            f"trades={pr.avg_trades:.1f}"
        )

    return results


def build_phase2_params(
    phase1_results: list[ParamResult],
) -> list[dict[str, Any]]:
    """根据 Phase 1 结果构建 Phase 2 参数组合。

    策略：取每个参数维度的 Top-2 值，做交叉组合。
    """
    # 找到每个参数维度的最佳值
    best_per_param: dict[str, list[tuple[float, Any]]] = {}
    for pr in phase1_results:
        for key in PARAM_KEYS:
            val = pr.params.get(key)
            baseline_val = BASELINE.get(key)
            if val == baseline_val:
                continue  # 跳过 baseline 值
            if key not in best_per_param:
                best_per_param[key] = []
            best_per_param[key].append((pr.avg_return, val))

    # 每个参数维度取 Top-2
    top_values: dict[str, list[Any]] = {}
    for key, vals in best_per_param.items():
        vals.sort(key=lambda x: x[0], reverse=True)
        top_values[key] = [v for _, v in vals[:2]]
        print(
            f"  {key} 最佳值: {top_values[key]} "
            f"(returns: {[f'{r*100:+.2f}%' for r, _ in vals[:2]]})"
        )

    # 构建组合：取每个维度的最佳值做组合
    combos: list[dict[str, Any]] = []

    # 组合1：全部使用各维度第1名
    combo1 = {**BASELINE}
    for key, vals in top_values.items():
        combo1[key] = vals[0]
    combos.append(combo1)

    # 组合2：stop_loss + take_profit 最佳组合
    if "stop_loss_pct" in top_values and "take_profit_pct" in top_values:
        combo = {**BASELINE}
        combo["stop_loss_pct"] = top_values["stop_loss_pct"][0]
        combo["take_profit_pct"] = top_values["take_profit_pct"][0]
        combos.append(combo)

    # 组合3：trailing + take_profit
    if "trailing_stop_pct" in top_values and "take_profit_pct" in top_values:
        combo = {**BASELINE}
        combo["trailing_stop_pct"] = top_values["trailing_stop_pct"][0]
        combo["take_profit_pct"] = top_values["take_profit_pct"][0]
        combos.append(combo)

    # 组合4：stop_loss + trailing
    if "stop_loss_pct" in top_values and "trailing_stop_pct" in top_values:
        combo = {**BASELINE}
        combo["stop_loss_pct"] = top_values["stop_loss_pct"][0]
        combo["trailing_stop_pct"] = top_values["trailing_stop_pct"][0]
        combos.append(combo)

    # 组合5：stop_loss + take_profit + trailing
    if all(
        k in top_values
        for k in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct")
    ):
        combo = {**BASELINE}
        combo["stop_loss_pct"] = top_values["stop_loss_pct"][0]
        combo["take_profit_pct"] = top_values["take_profit_pct"][0]
        combo["trailing_stop_pct"] = top_values["trailing_stop_pct"][0]
        combos.append(combo)

    # 组合6：全部第1名 + ma_exit
    if "ma_exit_period" in top_values:
        combo = dict(combo1)
        combo["ma_exit_period"] = top_values["ma_exit_period"][0]
        combos.append(combo)

    # 组合7：全部第1名 + max_hold
    if "max_hold_days" in top_values:
        combo = dict(combo1)
        combo["max_hold_days"] = top_values["max_hold_days"][0]
        combos.append(combo)

    # 组合8：各维度第2名全部组合
    combo8 = {**BASELINE}
    for key, vals in top_values.items():
        if len(vals) > 1:
            combo8[key] = vals[1]
    if combo8 != BASELINE:
        combos.append(combo8)

    return combos


def _format_params(params: dict[str, Any]) -> str:
    """格式化参数为可读字符串。"""
    parts = []
    for key in PARAM_KEYS:
        val = params.get(key, BASELINE.get(key))
        if val != BASELINE.get(key):
            parts.append(f"{key}={val}")
    if not parts:
        return "baseline(仅缠论卖点)"
    return ", ".join(parts)


def print_results_table(
    title: str,
    results: list[ParamResult],
    stock_data: dict[str, tuple[Any, str]],
) -> None:
    """打印结果汇总表。"""
    print(f"\n{'=' * 100}")
    print(f"  {title}")
    print(f"{'=' * 100}")

    # 按平均收益率排序
    sorted_results = sorted(results, key=lambda x: x.avg_return, reverse=True)

    # 表头
    header = f"{'Rank':<5} {'Params':<55} {'AvgRet':>8} {'AvgDD':>8} {'Sharpe':>8} {'WinRate':>8} {'Trades':>7}"
    print(header)
    print("-" * 100)

    for rank, pr in enumerate(sorted_results, 1):
        label = _format_params(pr.params)
        print(
            f"{rank:<5} {label:<55} "
            f"{pr.avg_return * 100:>+7.2f}% "
            f"{pr.avg_drawdown * 100:>7.2f}% "
            f"{pr.avg_sharpe:>8.3f} "
            f"{pr.avg_win_rate * 100:>7.1f}% "
            f"{pr.avg_trades:>7.1f}"
        )

    # 详细：每个股票的收益率
    print(f"\n  --- 逐股明细 (Top 5 参数组合) ---")
    for rank, pr in enumerate(sorted_results[:5], 1):
        label = _format_params(pr.params)
        print(f"\n  #{rank}: {label}")
        for code, (_, name) in stock_data.items():
            m = pr.metrics.get(code)
            if m:
                print(
                    f"    {name:6s}({code}) "
                    f"ret={m.total_return * 100:>+7.2f}%  "
                    f"trades={m.trade_count:3d}  "
                    f"dd={m.max_drawdown * 100:.2f}%"
                )


def main(
    stocks: list[tuple[str, Market, str]] | None = None,
    count: int = 800,
    cash: float = 1_000_000,
    warmup_bars: int = 60,
) -> None:
    """主入口：运行两阶段参数优化。"""
    if stocks is None:
        stocks = DEFAULT_STOCKS

    print("=" * 100)
    print("  缠论三类买点策略 — 参数优化")
    print(f"  股票池: {len(stocks)} 只 | K线: {count} 根 | 资金: {cash:,.0f} 元")
    print("=" * 100)

    # 1. 获取数据
    print("\n>> Step 1: 获取股票数据")
    with get_mac_client() as client:
        stock_data = fetch_stock_data(client, stocks, count=count)

    if not stock_data:
        print("ERROR: 未能获取任何股票数据")
        return

    print(f"\n  成功获取 {len(stock_data)}/{len(stocks)} 只股票数据")

    # 2. Phase 1: 逐参数扫描
    print(f"\n>> Step 2: Phase 1 — 逐参数扫描 ({len(PHASE1_PARAMS)} 组)")
    t0 = time.time()
    phase1_results = run_phase(
        "Phase1", PHASE1_PARAMS, stock_data, cash=cash, warmup_bars=warmup_bars
    )
    print(f"\n  Phase 1 耗时: {time.time() - t0:.1f}s")

    print_results_table("Phase 1 结果", phase1_results, stock_data)

    # 3. Phase 2: 组合优化
    print(f"\n>> Step 3: Phase 2 — 组合优化")
    phase2_params = build_phase2_params(phase1_results)
    print(f"\n  生成 {len(phase2_params)} 组合参数")

    if phase2_params:
        t0 = time.time()
        phase2_results = run_phase(
            "Phase2", phase2_params, stock_data, cash=cash, warmup_bars=warmup_bars
        )
        print(f"\n  Phase 2 耗时: {time.time() - t0:.1f}s")

        print_results_table("Phase 2 结果（含 Phase 1 baseline 对比）", phase2_results, stock_data)

        # 最终推荐
        all_results = phase1_results + phase2_results
        best = max(all_results, key=lambda x: x.avg_return)
        print(f"\n{'=' * 100}")
        print(f"  ★ 最优参数组合 ★")
        print(f"{'=' * 100}")
        print(f"  参数: {_format_params(best.params)}")
        print(f"  平均收益率: {best.avg_return * 100:+.2f}%")
        print(f"  平均最大回撤: {best.avg_drawdown * 100:.2f}%")
        print(f"  平均夏普比率: {best.avg_sharpe:.3f}")
        print(f"  平均胜率: {best.avg_win_rate * 100:.1f}%")
        print(f"  平均交易次数: {best.avg_trades:.1f}")
        print(f"\n  完整参数:")
        for key in PARAM_KEYS:
            print(f"    {key}: {best.params.get(key)}")
    else:
        print("  无可组合参数，跳过 Phase 2")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="缠论三类买点策略参数优化")
    parser.add_argument(
        "--count", type=int, default=800, help="K线数量 (默认 800)"
    )
    parser.add_argument(
        "--cash", type=float, default=1_000_000, help="初始资金 (默认 1000000)"
    )
    parser.add_argument(
        "--warmup-bars", type=int, default=60, help="预热K线数 (默认 60)"
    )
    args = parser.parse_args()

    main(
        count=args.count,
        cash=args.cash,
        warmup_bars=args.warmup_bars,
    )
