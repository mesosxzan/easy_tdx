"""亏损交易深度分析脚本（MAE/MFE + 退出效率）。

对多只个股运行 shadow_yang_v2 策略回测，对每笔交易计算：

  - MAE（Max Adverse Excursion）：持仓期间最大不利偏移（相对买入价的最大跌幅）
  - MFE（Max Favorable Excursion）：持仓期间最大有利偏移（相对买入价的最大涨幅）

基于 MAE/MFE 将亏损交易分类，识别可优化的退出与入场环节::

  - 到手即亏（dead_on_arrival）：MFE <= 0%，买入后从未盈利 -> 入场质量问题
  - 盈转亏（profit_to_loss）：MFE > 阈值但最终亏损 -> 退出过晚/追踪过松
  - 边际亏损（marginal）：MFE 较小且小亏 -> 噪声交易

同时对盈利交易计算退出效率（final_return / MFE），识别「截断利润」的赢家
（MFE 远高于实际收益），为放宽追踪止盈提供数据支撑。

用法::

    python scripts/analyze_loss_trades.py
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# 确保项目 src 可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategy import Strategy  # noqa: E402
from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────────

STRATEGY_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"
KLINE_COUNT = 1000
CASH = 100000.0
COMMISSION = 0.0003

# 盈转亏判定阈值：MFE 超过此值说明曾明显盈利，最终亏损 = 退出过晚
PROFIT_TO_LOSS_MFE_THRESHOLD = 3.0  # %
# 边际亏损阈值：MFE 与亏损都低于此值视为噪声
MARGINAL_THRESHOLD = 2.0  # %

# 股票池：覆盖不同风格（趋势/震荡/下跌/慢牛）
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


# ── 交易分析数据类 ──────────────────────────────────────────────────────────


@dataclass
class TradeAnalysis:
    """单笔交易的 MAE/MFE 分析结果。"""

    stock: str
    stock_type: str
    buy_date: str
    sell_date: str
    buy_reason: str
    sell_reason: str
    buy_price: float
    sell_price: float
    final_return_pct: float
    is_win: bool
    holding_bars: int

    # MAE/MFE（百分比，正数）
    mae_pct: float  # 最大不利偏移（买入后最大跌幅）
    mfe_pct: float  # 最大有利偏移（买入后最大涨幅）

    # 亏损类型分类
    loss_type: str  # dead_on_arrival / profit_to_loss / marginal / winner

    # 退出效率（仅对 MFE > 0 的交易有意义）
    exit_efficiency: float  # final_return / mfe（捕获了多少最大利润，可能为负）


# ── 策略加载 ────────────────────────────────────────────────────────────────


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


# ── MAE/MFE 计算 ────────────────────────────────────────────────────────────


def _classify_loss(
    mfe_pct: float, mae_pct: float, final_return_pct: float, is_win: bool
) -> str:
    """根据 MAE/MFE 分类交易类型。

    Returns:
        dead_on_arrival: 从未盈利（MFE <= 0），纯入场质量问题
        profit_to_loss: 曾明显盈利（MFE > 阈值）但最终亏损，退出过晚
        marginal: 小幅波动小亏，噪声交易
        winner: 盈利交易
    """
    if is_win:
        return "winner"
    # 亏损交易分类
    if mfe_pct <= 0:
        return "dead_on_arrival"
    if mfe_pct >= PROFIT_TO_LOSS_MFE_THRESHOLD:
        return "profit_to_loss"
    if mae_pct <= MARGINAL_THRESHOLD and mfe_pct < MARGINAL_THRESHOLD:
        return "marginal"
    return "marginal"


def _compute_mae_mfe(
    df: pd.DataFrame, buy_idx: int, sell_idx: int, buy_price: float
) -> tuple[float, float]:
    """计算持仓期间的 MAE/MFE（百分比）。

    Args:
        df: K 线数据
        buy_idx: 买入日在 df 中的索引
        sell_idx: 卖出日在 df 中的索引
        buy_price: 买入价格

    Returns:
        (mae_pct, mfe_pct)：均为正数百分比
    """
    if buy_price <= 0 or sell_idx <= buy_idx:
        return 0.0, 0.0

    # 持仓区间（买入日到卖出日，含两端）
    segment = df.iloc[buy_idx : sell_idx + 1]
    if segment.empty:
        return 0.0, 0.0

    min_low = float(segment["low"].min())
    max_high = float(segment["high"].max())

    mae_pct = max(0.0, (buy_price - min_low) / buy_price * 100)
    mfe_pct = max(0.0, (max_high - buy_price) / buy_price * 100)
    return mae_pct, mfe_pct


def _find_kline_index(df: pd.DataFrame, trade_dt: Any) -> int:
    """根据交易日期在 K 线中定位索引。"""
    dt_col = pd.to_datetime(df["datetime"])
    target = pd.Timestamp(trade_dt)
    matches = dt_col[dt_col == target].index.tolist()
    if matches:
        return int(matches[0])
    # fallback: 最近日期
    diffs = (dt_col - target).abs()
    return int(diffs.idxmin())


def _extract_trade_analyses(
    df: pd.DataFrame, result: Any, code: str, stock_type: str
) -> list[TradeAnalysis]:
    """从回测结果提取每笔交易的 MAE/MFE 分析。"""
    trades = result.trades
    if trades.empty:
        return []

    analyses: list[TradeAnalysis] = []
    buy_rows = trades[(trades["direction"] == "BUY") & (~trades.get("rejected", False))].copy()

    for _, buy_row in buy_rows.iterrows():
        buy_dt = pd.Timestamp(buy_row["datetime"])
        sell_rows = trades[
            (trades["direction"] == "SELL")
            & (pd.to_datetime(trades["datetime"]) >= buy_dt)
            & (~trades.get("rejected", False))
        ]
        if sell_rows.empty:
            continue

        sell_row = sell_rows.iloc[0]
        buy_price = float(buy_row["price"])
        sell_price = float(sell_row["price"])

        # 计算收益率（优先用 pnl/cost_basis）
        cost_basis = float(sell_row.get("cost_basis", 0))
        pnl = float(sell_row.get("pnl", 0))
        if cost_basis > 0:
            final_return_pct = pnl / cost_basis * 100
        else:
            final_return_pct = (sell_price - buy_price) / buy_price * 100

        # 定位 K 线索引计算 MAE/MFE
        buy_idx = _find_kline_index(df, buy_dt)
        sell_idx = _find_kline_index(df, sell_row["datetime"])
        mae_pct, mfe_pct = _compute_mae_mfe(df, buy_idx, sell_idx, buy_price)

        is_win = final_return_pct > 0
        holding_bars = max(0, sell_idx - buy_idx)
        loss_type = _classify_loss(mfe_pct, mae_pct, final_return_pct, is_win)
        exit_efficiency = (
            final_return_pct / mfe_pct * 100 if mfe_pct > 0.01 else 0.0
        )

        analyses.append(
            TradeAnalysis(
                stock=code,
                stock_type=stock_type,
                buy_date=str(buy_row["datetime"]).split(" ")[0].replace("-", ""),
                sell_date=str(sell_row["datetime"]).split(" ")[0].replace("-", ""),
                buy_reason=str(buy_row.get("reason", "")),
                sell_reason=str(sell_row.get("reason", "")),
                buy_price=buy_price,
                sell_price=sell_price,
                final_return_pct=final_return_pct,
                is_win=is_win,
                holding_bars=holding_bars,
                mae_pct=mae_pct,
                mfe_pct=mfe_pct,
                loss_type=loss_type,
                exit_efficiency=exit_efficiency,
            )
        )

    return analyses


# ── 回测 ────────────────────────────────────────────────────────────────────


def _run_backtest(
    client: MacClient,
    code: str,
    market_str: str,
    strategy_cls: type[Strategy],
) -> tuple[pd.DataFrame, Any]:
    """获取数据并运行回测。"""
    df = client.get_stock_kline(
        parse_market(market_str),
        code,
        period=parse_period("DAILY"),
        start=0,
        count=KLINE_COUNT,
        adjust=parse_adjust("QFQ"),
    )
    engine = BacktestEngine(strategy=strategy_cls, cash=CASH, commission=COMMISSION)
    return df, engine.run(df)


# ── 分析输出 ────────────────────────────────────────────────────────────────


def _print_overview(df: pd.DataFrame) -> None:
    """打印整体概览。"""
    total = len(df)
    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]
    win_rate = len(wins) / total if total > 0 else 0
    avg_win = wins["final_return_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["final_return_pct"].mean() if len(losses) > 0 else 0
    gross_profit = wins["final_return_pct"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["final_return_pct"].sum()) if len(losses) > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    print(f"\n{'=' * 90}")
    print("整体交易概览")
    print(f"{'=' * 90}")
    print(f"  总交易: {total} | 盈利 {len(wins)} | 亏损 {len(losses)} | 胜率 {win_rate:.1%}")
    print(f"  均盈 {avg_win:.2f}% | 均亏 {avg_loss:.2f}%")
    print(f"  总盈利 {gross_profit:.2f}% | 总亏损 {gross_loss:.2f}% | 盈亏比 {profit_factor:.2f}")
    print(f"  均MAE {df['mae_pct'].mean():.2f}% | 均MFE {df['mfe_pct'].mean():.2f}%")
    print(f"  均持仓 {df['holding_bars'].mean():.1f} 天")


def _print_loss_type_analysis(df: pd.DataFrame) -> None:
    """打印亏损类型构成分析。"""
    losses = df[~df["is_win"]]
    print(f"\n{'=' * 90}")
    print(f"亏损类型构成（共 {len(losses)} 笔亏损）")
    print(f"{'=' * 90}")

    type_order = ["dead_on_arrival", "profit_to_loss", "marginal"]
    type_names = {
        "dead_on_arrival": "到手即亏（从未盈利）",
        "profit_to_loss": "盈转亏（曾盈利后反转）",
        "marginal": "边际亏损（小幅噪声）",
    }

    print(f"\n{'类型':<24} {'笔数':>6} {'占比':>8} {'均亏%':>8} "
          f"{'均MFE%':>8} {'均MAE%':>8} {'总亏损%':>10} {'优化方向':<16}")
    print("-" * 100)

    total_loss = abs(losses["final_return_pct"].sum())
    for loss_type in type_order:
        subset = losses[losses["loss_type"] == loss_type]
        if len(subset) == 0:
            continue
        pct = len(subset) / len(losses) * 100
        avg_loss = subset["final_return_pct"].mean()
        avg_mfe = subset["mfe_pct"].mean()
        avg_mae = subset["mae_pct"].mean()
        total = abs(subset["final_return_pct"].sum())
        total_pct = total / total_loss * 100 if total_loss > 0 else 0
        direction = {
            "dead_on_arrival": "优化入场过滤",
            "profit_to_loss": "优化退出/追踪",
            "marginal": "提高信号质量",
        }[loss_type]
        print(f"{type_names[loss_type]:<24} {len(subset):>6} {pct:>7.1f}% "
              f"{avg_loss:>8.2f} {avg_mfe:>8.2f} {avg_mae:>8.2f} "
              f"{total:>9.2f}% {total_pct:>8.1f}%  {direction}")

    # 盈转亏交易明细（最值得优化）
    profit_to_loss = losses[losses["loss_type"] == "profit_to_loss"]
    if len(profit_to_loss) > 0:
        header = (
            "--- 盈转亏明细（曾盈利 "
            f"{PROFIT_TO_LOSS_MFE_THRESHOLD}%+ 后亏损，退出过晚）---"
        )
        print(f"\n{header}")
        print(f"{'股票':<8} {'买入日':>10} {'信号':<14} {'退出原因':<14} {'买入价':>8} "
              f"{'卖出价':>8} {'盈亏%':>7} {'MFE%':>7} {'MAE%':>7} {'持仓':>5}")
        print("-" * 100)
        for _, row in profit_to_loss.sort_values("final_return_pct").iterrows():
            print(f"{row['stock']:<8} {row['buy_date']:>10} {row['buy_reason']:<14} "
                  f"{row['sell_reason']:<14} {row['buy_price']:>8.2f} {row['sell_price']:>8.2f} "
                  f"{row['final_return_pct']:>7.2f} {row['mfe_pct']:>7.2f} "
                  f"{row['mae_pct']:>7.2f} {row['holding_bars']:>5}")

    # 到手即亏交易明细（入场质量问题）
    dead = losses[losses["loss_type"] == "dead_on_arrival"]
    if len(dead) > 0:
        print("\n--- 到手即亏交易明细（MFE<=0%，从未盈利，入场质量问题）---")
        print(f"{'股票':<8} {'买入日':>10} {'信号':<14} {'退出原因':<14} {'买入价':>8} "
              f"{'卖出价':>8} {'盈亏%':>7} {'MFE%':>7} {'MAE%':>7} {'持仓':>5}")
        print("-" * 100)
        for _, row in dead.sort_values("final_return_pct").iterrows():
            print(f"{row['stock']:<8} {row['buy_date']:>10} {row['buy_reason']:<14} "
                  f"{row['sell_reason']:<14} {row['buy_price']:>8.2f} {row['sell_price']:>8.2f} "
                  f"{row['final_return_pct']:>7.2f} {row['mfe_pct']:>7.2f} "
                  f"{row['mae_pct']:>7.2f} {row['holding_bars']:>5}")


def _print_exit_reason_analysis(df: pd.DataFrame) -> None:
    """打印退出原因分布分析。"""
    print(f"\n{'=' * 90}")
    print("退出原因分析（盈亏分组）")
    print(f"{'=' * 90}")

    losses = df[~df["is_win"]]
    wins = df[df["is_win"]]

    print(f"\n{'退出原因':<16} {'亏损笔数':>8} {'均亏%':>8} {'总亏%':>9} "
          f"{'|':<2} {'盈利笔数':>8} {'均盈%':>8} {'总盈%':>9}")
    print("-" * 90)

    all_reasons = sorted(set(df["sell_reason"].dropna()))
    for reason in all_reasons:
        loss_sub = losses[losses["sell_reason"] == reason]
        win_sub = wins[wins["sell_reason"] == reason]
        l_avg = loss_sub["final_return_pct"].mean() if len(loss_sub) > 0 else 0
        l_sum = loss_sub["final_return_pct"].sum() if len(loss_sub) > 0 else 0
        w_avg = win_sub["final_return_pct"].mean() if len(win_sub) > 0 else 0
        w_sum = win_sub["final_return_pct"].sum() if len(win_sub) > 0 else 0
        print(f"{reason:<16} {len(loss_sub):>8} {l_avg:>8.2f} {l_sum:>9.2f} "
              f"{'|':<2} {len(win_sub):>8} {w_avg:>8.2f} {w_sum:>9.2f}")


def _print_winner_efficiency(df: pd.DataFrame) -> None:
    """打印盈利交易退出效率分析（识别截断利润的赢家）。"""
    wins = df[df["is_win"]].copy()
    wins = wins[wins["mfe_pct"] > 0.5]  # 排除 MFE 极小的

    print(f"\n{'=' * 90}")
    print(f"盈利交易退出效率（MFE 捕获率，共 {len(wins)} 笔）")
    print(f"{'=' * 90}")

    if wins.empty:
        print("  无足够数据")
        return

    # 退出效率 = 实际收益 / MFE（100% = 完美捕获最高点）
    print(f"\n  均退出效率: {wins['exit_efficiency'].mean():.1f}%")
    print(f"  中位退出效率: {wins['exit_efficiency'].median():.1f}%")
    avg_mfe = wins['mfe_pct'].mean()
    avg_ret = wins['final_return_pct'].mean()
    print(f"  均MFE: {avg_mfe:.2f}% | 均实际收益: {avg_ret:.2f}%")
    leftover = (wins['mfe_pct'] - wins['final_return_pct']).mean()
    print(f"  遗留利润（MFE - 实际）均值: {leftover:.2f}%")

    # 低效率赢家（捕获 < 40%，利润大量回吐）
    low_eff = wins[wins["exit_efficiency"] < 40]
    high_eff = wins[wins["exit_efficiency"] >= 40]
    low_mfe = low_eff['mfe_pct'].mean()
    low_ret = low_eff['final_return_pct'].mean()
    print(f"\n  低效率赢家（捕获<40%）: {len(low_eff)} 笔，"
          f"均MFE {low_mfe:.2f}% vs 均收益 {low_ret:.2f}%")
    high_mfe = high_eff['mfe_pct'].mean()
    high_ret = high_eff['final_return_pct'].mean()
    print(f"  高效率赢家（捕获>=40%）: {len(high_eff)} 笔，"
          f"均MFE {high_mfe:.2f}% vs 均收益 {high_ret:.2f}%")

    if len(low_eff) > 0:
        print("\n--- 低效率赢家明细（MFE 远高于实际收益，退出过早）---")
        print(f"{'股票':<8} {'买入日':>10} {'信号':<14} {'退出原因':<14} {'实际%':>7} "
              f"{'MFE%':>7} {'捕获%':>7} {'持仓':>5}")
        print("-" * 90)
        for _, row in low_eff.sort_values("exit_efficiency").iterrows():
            print(f"{row['stock']:<8} {row['buy_date']:>10} {row['buy_reason']:<14} "
                  f"{row['sell_reason']:<14} {row['final_return_pct']:>7.2f} "
                  f"{row['mfe_pct']:>7.2f} {row['exit_efficiency']:>7.1f} {row['holding_bars']:>5}")


def _print_mae_mfe_distribution(df: pd.DataFrame) -> None:
    """打印 MAE/MFE 分布分析（寻找最优止损位）。"""
    losses = df[~df["is_win"]]
    print(f"\n{'=' * 90}")
    print(f"MAE 分布分析（亏损交易，共 {len(losses)} 笔，寻找最优止损位）")
    print(f"{'=' * 90}")

    bins = [0, 3, 5, 8, 10, 15, 20, 100]
    print(f"\n{'MAE区间%':<14} {'笔数':>6} {'占比':>8} {'均亏%':>8} {'均MFE%':>8} {'类型分布':<30}")
    print("-" * 80)

    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        subset = losses[(losses["mae_pct"] >= lo) & (losses["mae_pct"] < hi)]
        if len(subset) == 0:
            continue
        pct = len(subset) / len(losses) * 100
        avg_loss = subset["final_return_pct"].mean()
        avg_mfe = subset["mfe_pct"].mean()
        type_counts = subset["loss_type"].value_counts()
        type_str = " / ".join(f"{t}:{n}" for t, n in type_counts.items())
        print(f"[{lo}, {hi}){'':<6} {len(subset):>6} {pct:>7.1f}% {avg_loss:>8.2f} "
              f"{avg_mfe:>8.2f} {type_str:<30}")

    # 止损位建议：MAE 累计分布
    print("\n--- 止损位累计分布（MAE <= X% 的亏损交易占比）---")
    for threshold in [3, 5, 6, 8, 10, 12, 15]:
        within = losses[losses["mae_pct"] <= threshold]
        if len(within) == 0:
            continue
        # 这些交易如果止损更紧，亏损会减少多少
        # 假设止损在 threshold%，则每笔亏损 = threshold%（而非实际亏损）
        actual_loss = abs(within["final_return_pct"].sum())
        capped_loss = len(within) * threshold
        saved = actual_loss - capped_loss
        pct = len(within) / len(losses) * 100
        print(f"  MAE <= {threshold:>2}%: {len(within)} 笔 ({pct:.0f}%)，"
              f"实际总亏 {actual_loss:.1f}%，若止损于{threshold}%仅亏 {capped_loss:.1f}%，"
              f"可省 {saved:.1f}%")

    print(f"\n{'=' * 90}")
    print("MFE 分布分析（亏损交易，识别盈转亏幅度）")
    print(f"{'=' * 90}")
    print(f"\n{'MFE区间%':<14} {'笔数':>6} {'占比':>8} {'均亏%':>8} {'均MAE%':>8} {'类型分布':<30}")
    print("-" * 80)
    mfe_bins: list[float] = [0, 0.01, 2, 3, 5, 8, 12, 100]
    for i in range(len(mfe_bins) - 1):
        lo_f, hi_f = mfe_bins[i], mfe_bins[i + 1]
        subset = losses[(losses["mfe_pct"] >= lo_f) & (losses["mfe_pct"] < hi_f)]
        if len(subset) == 0:
            continue
        pct = len(subset) / len(losses) * 100
        avg_loss = subset["final_return_pct"].mean()
        avg_mae = subset["mae_pct"].mean()
        type_counts = subset["loss_type"].value_counts()
        type_str = " / ".join(f"{t}:{n}" for t, n in type_counts.items())
        print(f"[{lo_f}, {hi_f}){'':<6} {len(subset):>6} {pct:>7.1f}% {avg_loss:>8.2f} "
              f"{avg_mae:>8.2f} {type_str:<30}")


def _print_holding_period_analysis(df: pd.DataFrame) -> None:
    """打印持仓天数与盈亏关系分析。"""
    print(f"\n{'=' * 90}")
    print("持仓天数与盈亏关系")
    print(f"{'=' * 90}")
    print(f"\n{'持仓天数':<14} {'笔数':>6} {'胜率':>8} {'均盈亏%':>9} {'均MFE%':>8} {'均MAE%':>8}")
    print("-" * 60)

    bins = [(0, 3), (3, 5), (5, 10), (10, 20), (20, 40), (40, 1000)]
    for lo, hi in bins:
        subset = df[(df["holding_bars"] >= lo) & (df["holding_bars"] < hi)]
        if len(subset) == 0:
            continue
        win_rate = subset["is_win"].mean()
        avg_ret = subset["final_return_pct"].mean()
        avg_mfe = subset["mfe_pct"].mean()
        avg_mae = subset["mae_pct"].mean()
        label = f"{lo}-{hi}" if hi < 1000 else f"{lo}+"
        print(f"{label:<14} {len(subset):>6} {win_rate:>7.1%} {avg_ret:>+9.2f} "
              f"{avg_mfe:>8.2f} {avg_mae:>8.2f}")


def _print_optimization_suggestions(df: pd.DataFrame) -> None:
    """基于 MAE/MFE 分析输出优化建议。"""
    losses = df[~df["is_win"]]
    wins = df[df["is_win"]]
    total_loss = abs(losses["final_return_pct"].sum())
    total_profit = wins["final_return_pct"].sum()
    profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

    print(f"\n{'=' * 90}")
    print("优化建议（基于 MAE/MFE 数据驱动分析）")
    print(f"{'=' * 90}")
    print(f"\n当前: 胜率 {len(wins)/len(df):.1%} | 盈亏比 {profit_factor:.2f} | "
          f"总盈 {total_profit:.1f}% | 总亏 {total_loss:.1f}%")

    suggestions: list[str] = []

    # 1. 盈转亏占比
    p2l = losses[losses["loss_type"] == "profit_to_loss"]
    if len(p2l) > 0:
        p2l_loss = abs(p2l["final_return_pct"].sum())
        p2l_lost_profit = (p2l["mfe_pct"] - p2l["final_return_pct"]).sum()
        suggestions.append(
            f"\n  [退出优化] 盈转亏交易 {len(p2l)} 笔（占亏损 {len(p2l)/len(losses)*100:.0f}%），"
            f"总亏 {p2l_loss:.1f}%。这些交易曾盈利 {p2l['mfe_pct'].mean():.1f}%+ 但最终亏损，"
            f"说明追踪止盈过松/退出过晚。若在 MFE 的一半处止盈，可挽回约 "
            f"{p2l_lost_profit/2:.1f}% 利润。"
        )

    # 2. 到手即亏占比
    dead = losses[losses["loss_type"] == "dead_on_arrival"]
    if len(dead) > 0:
        dead_loss = abs(dead["final_return_pct"].sum())
        dead_pct = len(dead) / len(losses) * 100
        suggestions.append(
            f"\n  [入场优化] 到手即亏交易 {len(dead)} 笔（占亏损 {dead_pct:.0f}%），"
            f"总亏 {dead_loss:.1f}%。这些交易买入后从未盈利（MFE<=0），"
            f"纯入场质量问题，需加强买入过滤。"
        )

    # 3. 止损位分析
    # 找到能覆盖大部分亏损交易但不过紧的止损位
    for threshold in [5, 6, 8]:
        within = losses[losses["mae_pct"] <= threshold]
        if len(within) == 0:
            continue
        actual = abs(within["final_return_pct"].sum())
        capped = len(within) * threshold
        over = losses[losses["mae_pct"] > threshold]
        over_actual = abs(over["final_return_pct"].sum()) if len(over) > 0 else 0
        over_capped = len(over) * threshold if len(over) > 0 else 0
        total_saved = (actual - capped) + (over_actual - over_capped)
        if total_saved > 0:
            suggestions.append(
                f"\n  [止损优化] 若硬止损设为 {threshold}%：MAE <= {threshold}% 的 "
                f"{len(within)} 笔可省 {actual - capped:.1f}%，"
                f"MAE > {threshold}% 的 {len(over)} 笔可省 {over_actual - over_capped:.1f}%，"
                f"合计减少亏损约 {total_saved:.1f}%。"
            )
            break

    # 4. 赢家退出效率
    low_eff_wins = wins[(wins["exit_efficiency"] < 40) & (wins["mfe_pct"] > 5)]
    if len(low_eff_wins) > 0:
        lost_profit = (low_eff_wins["mfe_pct"] - low_eff_wins["final_return_pct"]).sum()
        low_mfe_avg = low_eff_wins['mfe_pct'].mean()
        low_ret_avg = low_eff_wins['final_return_pct'].mean()
        suggestions.append(
            f"\n  [止盈优化] {len(low_eff_wins)} 笔赢家退出效率 < 40%（MFE 均值 "
            f"{low_mfe_avg:.1f}% vs 实际 {low_ret_avg:.1f}%），"
            f"遗留利润约 {lost_profit:.1f}%。放宽追踪止盈可增加盈亏比。"
        )

    # 5. 退出原因中亏损占比大的
    reason_loss = losses.groupby("sell_reason")["final_return_pct"].sum().abs().sort_values(
        ascending=False
    )
    worst_reason = reason_loss.index[0] if len(reason_loss) > 0 else ""
    if worst_reason:
        worst_count = len(losses[losses["sell_reason"] == worst_reason])
        suggestions.append(
            f"\n  [退出机制] '{worst_reason}' 是最大亏损来源（{worst_count} 笔亏损 "
            f"{reason_loss.iloc[0]:.1f}%），建议审查该退出路径的触发条件。"
        )

    if suggestions:
        for s in suggestions:
            print(s)
    else:
        print("  未发现明显优化机会。")


def main() -> None:
    print("=" * 90)
    print("亏损交易深度分析（MAE/MFE + 退出效率）")
    print(f"策略: {STRATEGY_FILE.name} | K线: {KLINE_COUNT} | 股票: {len(STOCK_POOL)} 只")
    print("=" * 90)

    strategy_cls = _load_strategy_class(STRATEGY_FILE)
    print(f"策略类: {strategy_cls.__name__}")

    print("\n正在连接行情服务器...")
    client = MacClient.from_best_host()
    client.connect()

    all_analyses: list[TradeAnalysis] = []

    try:
        print(f"\n{'=' * 90}")
        print("回测结果摘要")
        print(f"{'=' * 90}")

        for code, market_str, stock_type in STOCK_POOL:
            try:
                df, result = _run_backtest(client, code, market_str, strategy_cls)
                perf = result.performance
                print(
                    f"  {code} ({stock_type}): "
                    f"收益={perf.get('total_return', 0):>8.2%} "
                    f"胜率={perf.get('win_rate', 0):>5.1%} "
                    f"交易={perf.get('total_trades', 0):>3.0f} "
                    f"盈亏比={perf.get('profit_factor', 0):>5.2f}"
                )
                analyses = _extract_trade_analyses(df, result, code, stock_type)
                all_analyses.extend(analyses)
            except Exception as e:
                print(f"  {code} ({stock_type}): 错误 - {e}")
    finally:
        client.close()

    if not all_analyses:
        print("\n[!] 未提取到任何交易数据。")
        return

    df = pd.DataFrame([a.__dict__ for a in all_analyses])
    print(f"\n共分析 {len(df)} 笔交易")

    _print_overview(df)
    _print_loss_type_analysis(df)
    _print_exit_reason_analysis(df)
    _print_winner_efficiency(df)
    _print_mae_mfe_distribution(df)
    _print_holding_period_analysis(df)
    _print_optimization_suggestions(df)

    print(f"\n{'=' * 90}")
    print("分析完成")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
