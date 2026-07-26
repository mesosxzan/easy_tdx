"""买入阳线特征提取与统计分析脚本。

对多只个股运行 shadow_yang_v2 策略回测，提取每笔买入交易信号日的 K 线特征，
按盈利/亏损分组统计，挖掘区分胜败交易的关键阳线特征。

用法::

    python scripts/analyze_buy_features.py

输出:
  1. 每只股票的回测绩效 + 交易明细
  2. 全部买入交易的特征汇总表（含盈亏标签）
  3. 盈利 vs 亏损交易的特征分布对比
  4. 关键区分特征识别与优化建议
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 确保项目 src 可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx import MyTT  # noqa: E402
from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategy import Strategy  # noqa: E402
from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────────

STRATEGY_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"
KLINE_COUNT = 1000
CASH = 100000.0
COMMISSION = 0.0003

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


# ── 特征提取 ────────────────────────────────────────────────────────────────


@dataclass
class TradeFeatures:
    """单笔买入交易的特征集合。"""

    # 基础信息
    stock: str
    stock_type: str
    buy_date: str
    sell_date: str
    buy_reason: str
    buy_price: float
    sell_price: float
    profit_pct: float
    is_win: bool
    holding_bars: int

    # 信号日 K 线实体特征
    body_pct: float          # (close-open)/open * 100
    body_ratio: float        # |close-open| / (high-low)
    body_abs: float          # close - open

    # 信号日影线特征
    upper_shadow_pct: float  # (high-max(o,c)) / open * 100
    lower_shadow_pct: float  # (min(o,c)-low) / open * 100
    upper_shadow_ratio: float  # upper_shadow / |body|
    lower_shadow_ratio: float  # lower_shadow / |body|

    # 前日阴线特征
    prev_body_pct: float     # 前日阴线跌幅
    prev_body_ratio: float   # 前日实体占振幅比
    reversal_ratio: float    # cur_body / prev_body（反转力度）

    # 成交量特征
    vol_ratio: float         # vol / vol_ma5
    vol_change_pct: float    # (vol - prev_vol) / prev_vol * 100

    # 均线位置特征
    vs_ma5: float            # (close-ma5)/ma5 * 100
    vs_ma10: float
    vs_ma20: float
    vs_ma60: float

    # 均线排列
    is_bullish_align: bool   # MA5>MA20>MA60 且 MA10>MA20>MA60
    ma5_above_ma10: bool

    # 趋势特征
    adx: float
    pdi: float
    mdi: float
    pdi_mdi_ratio: float     # +DI / -DI
    is_strong_trend: bool    # ADX>=30 且 +DI>-DI

    # 涨幅特征
    recent_gain3: float      # 近3日涨幅
    recent_gain5: float      # 近5日涨幅
    dev_ma20: float          # 偏离MA20百分比

    # 形态特征
    is_piercing: bool
    is_engulfing: bool
    is_morning_star: bool
    is_strong_reversal: bool
    has_lower_shadow: bool   # 有下影线（回收型阳线）


def _safe_div(a: float, b: float) -> float:
    """安全除法，b=0 返回 0。"""
    return a / b if abs(b) > 1e-9 else 0.0


def _extract_features(
    stock: str,
    stock_type: str,
    df: pd.DataFrame,
    buy_row: pd.Series,
    sell_row: pd.Series,
) -> TradeFeatures:
    """从 K 线数据中提取买入信号日的特征。

    信号日 = 买入执行日的前一根 K 线（next_open 执行模型下，
    信号在 T 日生成，T+1 日开盘成交）。
    """
    # 找到买入执行日在 K 线中的位置（datetime 为 Timestamp 类型）
    buy_dt = pd.Timestamp(buy_row["datetime"])
    dt_col = pd.to_datetime(df["datetime"])
    matches = dt_col[dt_col == buy_dt].index.tolist()
    if not matches:
        # 尝试找最近的
        diffs = (dt_col - buy_dt).abs()
        matches = [int(diffs.idxmin())]
    exec_idx = matches[0]

    # 交易日期即为信号日（strategy 信号在当根 K 线生成并成交）
    signal_idx = exec_idx
    if signal_idx < 5:
        signal_idx = exec_idx  # fallback

    # 提取 K 线数据
    o = float(df.iloc[signal_idx]["open"])
    c = float(df.iloc[signal_idx]["close"])
    h = float(df.iloc[signal_idx]["high"])
    low_val = float(df.iloc[signal_idx]["low"])
    v = float(df.iloc[signal_idx]["vol"])

    # 前日数据
    prev_o = float(df.iloc[signal_idx - 1]["open"])
    prev_c = float(df.iloc[signal_idx - 1]["close"])
    prev_h = float(df.iloc[signal_idx - 1]["high"])
    prev_l = float(df.iloc[signal_idx - 1]["low"])
    prev_v = float(df.iloc[signal_idx - 1]["vol"])

    # 前前日数据（晨星需要）
    prev2_o = float(df.iloc[signal_idx - 2]["open"]) if signal_idx >= 2 else prev_o
    prev2_c = float(df.iloc[signal_idx - 2]["close"]) if signal_idx >= 2 else prev_c

    # ── 实体特征 ──
    body_abs = c - o
    body_pct = _safe_div(c - o, o) * 100
    full_range = h - low_val
    body_ratio = _safe_div(abs(c - o), full_range)

    # ── 影线特征 ──
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - low_val
    upper_shadow_pct = _safe_div(upper_shadow, o) * 100
    lower_shadow_pct = _safe_div(lower_shadow, o) * 100
    body_val = abs(c - o)
    upper_shadow_ratio = _safe_div(upper_shadow, body_val)
    lower_shadow_ratio = _safe_div(lower_shadow, body_val)

    # ── 前日阴线特征 ──
    prev_body_pct = _safe_div(prev_o - prev_c, prev_o) * 100  # 阴线跌幅为正
    prev_body_ratio = _safe_div(abs(prev_o - prev_c), prev_h - prev_l)
    reversal_ratio = _safe_div(abs(c - o), abs(prev_o - prev_c)) if prev_o != prev_c else 0.0

    # ── 成交量特征 ──
    vol_ma5 = float(df.iloc[signal_idx - 5 : signal_idx]["vol"].mean()) if signal_idx >= 5 else v
    vol_ratio = _safe_div(v, vol_ma5)
    vol_change_pct = _safe_div(v - prev_v, prev_v) * 100

    # ── 均线特征 ──
    closes = df["close"].values
    ma5 = float(np.mean(closes[signal_idx - 4 : signal_idx + 1])) if signal_idx >= 4 else c
    ma10 = float(np.mean(closes[signal_idx - 9 : signal_idx + 1])) if signal_idx >= 9 else c
    ma20 = float(np.mean(closes[signal_idx - 19 : signal_idx + 1])) if signal_idx >= 19 else c
    ma60 = float(np.mean(closes[signal_idx - 59 : signal_idx + 1])) if signal_idx >= 59 else c

    vs_ma5 = _safe_div(c - ma5, ma5) * 100
    vs_ma10 = _safe_div(c - ma10, ma10) * 100
    vs_ma20 = _safe_div(c - ma20, ma20) * 100
    vs_ma60 = _safe_div(c - ma60, ma60) * 100

    is_bullish_align = ma5 > ma20 > ma60 and ma10 > ma20 > ma60
    ma5_above_ma10 = ma5 > ma10

    # ── 趋势特征 (ADX/DI) ──
    highs = df["high"].values
    lows = df["low"].values
    pdi_arr, mdi_arr, adx_arr, _ = MyTT.DMI(closes, highs, lows)
    adx = float(adx_arr[signal_idx]) if signal_idx < len(adx_arr) else 0.0
    pdi = float(pdi_arr[signal_idx]) if signal_idx < len(pdi_arr) else 0.0
    mdi = float(mdi_arr[signal_idx]) if signal_idx < len(mdi_arr) else 0.0
    pdi_mdi_ratio = _safe_div(pdi, mdi)
    is_strong_trend = adx >= 30.0 and pdi > mdi

    # ── 涨幅特征 ──
    close_3ago = float(closes[signal_idx - 3]) if signal_idx >= 3 else c
    close_5ago = float(closes[signal_idx - 5]) if signal_idx >= 5 else c
    recent_gain3 = _safe_div(c - close_3ago, close_3ago) * 100
    recent_gain5 = _safe_div(c - close_5ago, close_5ago) * 100
    dev_ma20 = vs_ma20  # 偏离MA20

    # ── 形态特征 ──
    is_piercing = _check_piercing(o, c, prev_o, prev_c, prev_l)
    is_engulfing = _check_engulfing(o, c, prev_o, prev_c)
    is_morning_star = _check_morning_star(o, c, prev_o, prev_c, prev2_o, prev2_c)
    is_strong_reversal = _check_strong_reversal(o, c, prev_o, prev_c)
    has_lower_shadow = lower_shadow > body_val * 0.3  # 下影线明显

    # ── 盈亏计算 ──
    buy_price = float(buy_row["price"])
    sell_price = float(sell_row["price"])
    # 优先用 pnl/cost_basis，fallback 到价格差
    cost_basis = float(sell_row.get("cost_basis", 0))
    pnl = float(sell_row.get("pnl", 0))
    if cost_basis > 0:
        profit_pct = _safe_div(pnl, cost_basis) * 100
    else:
        profit_pct = _safe_div(sell_price - buy_price, buy_price) * 100

    holding_bars = int(
        (pd.Timestamp(sell_row["datetime"]) - pd.Timestamp(buy_row["datetime"])).days
    )

    buy_date_str = str(buy_row["datetime"]).split(" ")[0].replace("-", "")
    sell_date_str = str(sell_row["datetime"]).split(" ")[0].replace("-", "")

    return TradeFeatures(
        stock=stock,
        stock_type=stock_type,
        buy_date=buy_date_str,
        sell_date=sell_date_str,
        buy_reason=str(buy_row.get("reason", "")),
        buy_price=buy_price,
        sell_price=sell_price,
        profit_pct=profit_pct,
        is_win=profit_pct > 0,
        holding_bars=holding_bars,
        body_pct=body_pct,
        body_ratio=body_ratio,
        body_abs=body_abs,
        upper_shadow_pct=upper_shadow_pct,
        lower_shadow_pct=lower_shadow_pct,
        upper_shadow_ratio=upper_shadow_ratio,
        lower_shadow_ratio=lower_shadow_ratio,
        prev_body_pct=prev_body_pct,
        prev_body_ratio=prev_body_ratio,
        reversal_ratio=reversal_ratio,
        vol_ratio=vol_ratio,
        vol_change_pct=vol_change_pct,
        vs_ma5=vs_ma5,
        vs_ma10=vs_ma10,
        vs_ma20=vs_ma20,
        vs_ma60=vs_ma60,
        is_bullish_align=is_bullish_align,
        ma5_above_ma10=ma5_above_ma10,
        adx=adx,
        pdi=pdi,
        mdi=mdi,
        pdi_mdi_ratio=pdi_mdi_ratio,
        is_strong_trend=is_strong_trend,
        recent_gain3=recent_gain3,
        recent_gain5=recent_gain5,
        dev_ma20=dev_ma20,
        is_piercing=is_piercing,
        is_engulfing=is_engulfing,
        is_morning_star=is_morning_star,
        is_strong_reversal=is_strong_reversal,
        has_lower_shadow=has_lower_shadow,
    )


def _check_piercing(
    cur_o: float, cur_c: float, prev_o: float, prev_c: float, prev_l: float
) -> bool:
    if prev_c >= prev_o:
        return False
    if cur_o >= prev_l:
        return False
    midpoint = (prev_o + prev_c) / 2
    return cur_c > midpoint and cur_c < prev_o


def _check_engulfing(cur_o: float, cur_c: float, prev_o: float, prev_c: float) -> bool:
    if prev_c >= prev_o:
        return False
    if cur_c <= cur_o:
        return False
    return cur_o <= prev_c and cur_c >= prev_o


def _check_morning_star(
    cur_o: float, cur_c: float, prev_o: float, prev_c: float, prev2_o: float, prev2_c: float
) -> bool:
    if prev2_c >= prev2_o:
        return False
    prev2_body = prev2_o - prev2_c
    prev_body = abs(prev_o - prev_c)
    if prev_body >= prev2_body * 0.5:
        return False
    if max(prev_o, prev_c) >= prev2_c:
        return False
    if cur_c <= cur_o:
        return False
    midpoint = (prev2_o + prev2_c) / 2
    return cur_c > midpoint


def _check_strong_reversal(cur_o: float, cur_c: float, prev_o: float, prev_c: float) -> bool:
    if prev_c >= prev_o:
        return False
    if cur_c <= cur_o:
        return False
    if cur_c < prev_o:
        return False
    prev_body = prev_o - prev_c
    cur_body = cur_c - cur_o
    return cur_body >= prev_body * 2.0


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


# ── 主流程 ──────────────────────────────────────────────────────────────────


def _run_backtest_for_stock(
    client: MacClient,
    code: str,
    market_str: str,
    stock_type: str,
    strategy_cls: type[Strategy],
) -> tuple[pd.DataFrame, Any]:
    """获取数据并运行回测，返回 (K线数据, 回测结果)。"""
    mkt = parse_market(market_str)
    df = client.get_stock_kline(
        mkt,
        code,
        period=parse_period("DAILY"),
        start=0,
        count=KLINE_COUNT,
        adjust=parse_adjust("QFQ"),
    )

    engine = BacktestEngine(strategy=strategy_cls, cash=CASH, commission=COMMISSION)
    result = engine.run(df)
    return df, result


def _extract_all_trades(
    df: pd.DataFrame,
    result: Any,
    code: str,
    stock_type: str,
) -> list[TradeFeatures]:
    """从回测结果中提取所有买入交易的特征。"""
    trades = result.trades
    if trades.empty:
        return []

    features_list: list[TradeFeatures] = []
    buy_rows = trades[trades["direction"] == "BUY"].copy()

    for _, buy_row in buy_rows.iterrows():
        if buy_row.get("rejected", False):
            continue

        buy_dt = pd.Timestamp(buy_row["datetime"])
        # 找到对应的卖出交易（买入之后的第一个 SELL）
        sell_rows = trades[
            (trades["direction"] == "SELL")
            & (pd.to_datetime(trades["datetime"]) >= buy_dt)
            & (~trades.get("rejected", False))
        ]
        if sell_rows.empty:
            continue

        sell_row = sell_rows.iloc[0]
        try:
            features = _extract_features(code, stock_type, df, buy_row, sell_row)
            features_list.append(features)
        except (IndexError, KeyError, ValueError) as e:
            print(f"  [!] {code} 买入 {buy_dt} 特征提取失败: {e}")

    return features_list


def _features_to_dataframe(features_list: list[TradeFeatures]) -> pd.DataFrame:
    """将特征列表转为 DataFrame。"""
    if not features_list:
        return pd.DataFrame()
    records = [f.__dict__ for f in features_list]
    return pd.DataFrame(records)


def _print_stock_summary(code: str, stock_type: str, result: Any) -> None:
    """打印单只股票的回测摘要。"""
    perf = result.performance
    total_return = perf.get("total_return", 0)
    sharpe = perf.get("sharpe", 0)
    win_rate = perf.get("win_rate", 0)
    total_trades = perf.get("total_trades", 0)
    profit_factor = perf.get("profit_factor", 0)
    max_dd = perf.get("max_drawdown", 0)

    print(
        f"  {code} ({stock_type}): "
        f"收益={total_return:>8.2%} 夏普={sharpe:>5.2f} "
        f"胜率={win_rate:>5.1%} 交易={total_trades:>3.0f} "
        f"盈亏比={profit_factor:>5.2f} 回撤={max_dd:>7.2%}"
    )


def _print_feature_comparison(df: pd.DataFrame) -> None:
    """打印盈利 vs 亏损交易的特征对比。"""
    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]

    print(f"\n{'=' * 90}")
    print(f"盈利 vs 亏损交易特征对比（盈利 {len(wins)} 笔 vs 亏损 {len(losses)} 笔）")
    print(f"{'=' * 90}")

    # 数值型特征对比
    numeric_features = [
        ("body_pct", "阳线实体涨幅%", "越高越好(强阳线)"),
        ("body_ratio", "实体占振幅比", "越高越好(实体饱满)"),
        ("upper_shadow_ratio", "上影线/实体比", "越低越好(无上影线压力)"),
        ("lower_shadow_ratio", "下影线/实体比", "越高越好(有下影线支撑)"),
        ("reversal_ratio", "反转力度(实体/前日)", "越高越好(反转力度强)"),
        ("prev_body_pct", "前日阴线跌幅%", "适中最好(跌太少无空间,跌太多恐继续)"),
        ("vol_ratio", "量比(vs 5日均量)", "越高越好(放量确认)"),
        ("vol_change_pct", "量变化%", "越高越好(放量)"),
        ("vs_ma5", "偏离MA5%", "适中最好"),
        ("vs_ma10", "偏离MA10%", "适中最好"),
        ("vs_ma20", "偏离MA20%", "适中最好(过低=弱,过高=追高)"),
        ("vs_ma60", "偏离MA60%", "适中最好"),
        ("adx", "ADX趋势强度", "越高越好(有趋势)"),
        ("pdi_mdi_ratio", "+DI/-DI比", "越高越好(多头强)"),
        ("recent_gain3", "近3日涨幅%", "越低越好(未追高)"),
        ("recent_gain5", "近5日涨幅%", "越低越好(未追高)"),
        ("dev_ma20", "偏离MA20%", "越低越好(未追高)"),
        ("holding_bars", "持仓天数", "盈亏差异"),
        ("profit_pct", "盈亏%", "盈亏差异"),
    ]

    print(
        f"\n{'特征':<22} {'说明':<24} {'盈利均值':>10} {'亏损均值':>10} "
        f"{'差异':>10} {'方向':<12}"
    )
    print("-" * 100)

    for col, desc, hint in numeric_features:
        if col not in df.columns:
            continue
        win_mean = wins[col].mean() if len(wins) > 0 else 0.0
        loss_mean = losses[col].mean() if len(losses) > 0 else 0.0
        diff = win_mean - loss_mean
        # 判断方向：正差异=盈利组更高
        if abs(diff) < 0.01:
            direction = "无明显差异"
        elif diff > 0:
            direction = "盈利>亏损"
        else:
            direction = "亏损>盈利"
        print(
            f"{col:<22} {hint:<24} {win_mean:>10.2f} {loss_mean:>10.2f} "
            f"{diff:>+10.2f} {direction:<12}"
        )

    # 布尔型特征对比（胜率）
    print(f"\n{'=' * 90}")
    print("布尔特征胜率分析")
    print(f"{'=' * 90}")
    bool_features = [
        ("is_bullish_align", "多头排列"),
        ("ma5_above_ma10", "MA5>MA10"),
        ("is_strong_trend", "强趋势(ADX>=30)"),
        ("is_piercing", "刺穿形态"),
        ("is_engulfing", "看涨吞没"),
        ("is_morning_star", "晨星形态"),
        ("is_strong_reversal", "强反转阳线"),
        ("has_lower_shadow", "有下影线"),
    ]

    print(f"\n{'特征':<22} {'说明':<16} {'该特征=True':>12} "
          f"{'该特征=False':>12} {'差异':>10} {'建议':<12}")
    print("-" * 90)

    for col, desc in bool_features:
        if col not in df.columns:
            continue
        true_trades = df[df[col]]
        false_trades = df[~df[col]]

        true_win_rate = true_trades["is_win"].mean() if len(true_trades) > 0 else 0.0
        false_win_rate = false_trades["is_win"].mean() if len(false_trades) > 0 else 0.0
        diff = true_win_rate - false_win_rate

        if abs(diff) < 0.05:
            advice = "无明显影响"
        elif diff > 0:
            advice = "倾向保留"
        else:
            advice = "倾向过滤"

        print(
            f"{col:<22} {desc:<16} {true_win_rate:>11.1%} {false_win_rate:>12.1%} "
            f"{diff:>+10.1%} {advice:<12}"
        )

    # 买入信号类型胜率分析
    print(f"\n{'=' * 90}")
    print("买入信号类型胜率分析")
    print(f"{'=' * 90}")
    if "buy_reason" in df.columns:
        reason_stats = df.groupby("buy_reason").agg(
            count=("is_win", "count"),
            wins=("is_win", "sum"),
            win_rate=("is_win", "mean"),
            avg_profit=("profit_pct", "mean"),
            avg_holding=("holding_bars", "mean"),
        )
        print(f"\n{'信号类型':<16} {'笔数':>6} {'盈利':>6} "
              f"{'胜率':>8} {'均盈亏%':>10} {'均持仓':>8}")
        print("-" * 60)
        for reason, row in reason_stats.iterrows():
            print(
                f"{reason:<16} {int(row['count']):>6} {int(row['wins']):>6} "
                f"{row['win_rate']:>7.1%} {row['avg_profit']:>10.2f} {row['avg_holding']:>8.0f}"
            )


def _print_distribution_analysis(df: pd.DataFrame) -> None:
    """打印关键特征的分布分析（寻找最优区间）。"""
    print(f"\n{'=' * 90}")
    print("关键特征区间胜率分析（寻找最优买入区间）")
    print(f"{'=' * 90}")

    features_to_bin = [
        ("body_pct", "阳线实体涨幅%", [-100, 1, 2, 3, 5, 8, 100]),
        ("reversal_ratio", "反转力度", [-100, 0.5, 1.0, 1.5, 2.0, 3.0, 100]),
        ("vol_ratio", "量比", [0, 0.8, 1.0, 1.2, 1.5, 2.0, 100]),
        ("adx", "ADX", [0, 15, 20, 25, 30, 40, 100]),
        ("recent_gain3", "近3日涨幅%", [-100, 0, 3, 6, 9, 12, 100]),
        ("recent_gain5", "近5日涨幅%", [-100, 0, 5, 10, 15, 20, 100]),
        ("dev_ma20", "偏离MA20%", [-100, 0, 5, 10, 15, 20, 25, 100]),
        ("vs_ma20", "vs MA20%", [-100, 0, 3, 5, 10, 15, 100]),
    ]

    for col, desc, bins in features_to_bin:
        if col not in df.columns:
            continue
        print(f"\n--- {desc} ({col}) ---")
        print(f"{'区间':<20} {'笔数':>6} {'胜率':>8} {'均盈亏%':>10} {'盈亏比':>10}")
        print("-" * 60)

        for i in range(len(bins) - 1):
            lo, hi = bins[i], bins[i + 1]
            mask = (df[col] >= lo) & (df[col] < hi)
            subset = df[mask]
            if len(subset) == 0:
                continue
            win_rate = subset["is_win"].mean()
            avg_profit = subset["profit_pct"].mean()
            wins_profit = subset[subset["is_win"]]["profit_pct"].sum()
            losses_profit = abs(subset[~subset["is_win"]]["profit_pct"].sum())
            pf = wins_profit / losses_profit if losses_profit > 0 else float("inf")
            label = f"[{lo:.1f}, {hi:.1f})"
            print(
                f"{label:<20} {len(subset):>6} {win_rate:>7.1%} {avg_profit:>10.2f} {pf:>10.2f}"
            )


def _print_optimization_suggestions(df: pd.DataFrame) -> None:
    """基于数据分析输出优化建议。"""
    print(f"\n{'=' * 90}")
    print("优化建议（基于数据驱动分析）")
    print(f"{'=' * 90}")

    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]
    total = len(df)
    overall_win_rate = len(wins) / total if total > 0 else 0

    print(f"\n整体: {total} 笔交易, 胜率 {overall_win_rate:.1%}, "
          f"均盈亏 {df['profit_pct'].mean():.2f}%, "
          f"盈利均赚 {wins['profit_pct'].mean():.2f}%, "
          f"亏损均亏 {losses['profit_pct'].mean():.2f}%")

    suggestions: list[str] = []

    # 1. 分析实体涨幅
    if "body_pct" in df.columns:
        for threshold in [2.0, 3.0, 5.0]:
            strong = df[df["body_pct"] >= threshold]
            weak = df[df["body_pct"] < threshold]
            if len(strong) > 2 and len(weak) > 2:
                strong_wr = strong["is_win"].mean()
                weak_wr = weak["is_win"].mean()
                if strong_wr > weak_wr + 0.1:
                    suggestions.append(
                        f"  - 阳线实体涨幅 >= {threshold}% 时胜率 {strong_wr:.1%} "
                        f"vs < {threshold}% 时 {weak_wr:.1%}，考虑增加最小实体涨幅过滤"
                    )
                    break

    # 2. 分析反转力度
    if "reversal_ratio" in df.columns:
        for threshold in [1.0, 1.5, 2.0]:
            strong = df[df["reversal_ratio"] >= threshold]
            weak = df[df["reversal_ratio"] < threshold]
            if len(strong) > 2 and len(weak) > 2:
                strong_wr = strong["is_win"].mean()
                weak_wr = weak["is_win"].mean()
                if strong_wr > weak_wr + 0.1:
                    suggestions.append(
                        f"  - 反转力度 >= {threshold} 时胜率 {strong_wr:.1%} "
                        f"vs < {threshold} 时 {weak_wr:.1%}，考虑增加最小反转力度过滤"
                    )
                    break

    # 3. 分析量比
    if "vol_ratio" in df.columns:
        for threshold in [1.0, 1.2, 1.5]:
            strong = df[df["vol_ratio"] >= threshold]
            weak = df[df["vol_ratio"] < threshold]
            if len(strong) > 2 and len(weak) > 2:
                strong_wr = strong["is_win"].mean()
                weak_wr = weak["is_win"].mean()
                if strong_wr > weak_wr + 0.1:
                    suggestions.append(
                        f"  - 量比 >= {threshold} 时胜率 {strong_wr:.1%} "
                        f"vs < {threshold} 时 {weak_wr:.1%}，考虑提高成交量确认阈值"
                    )
                    break

    # 4. 分析近3日涨幅（追高保护）
    if "recent_gain3" in df.columns:
        for threshold in [3.0, 6.0, 9.0]:
            low_gain = df[df["recent_gain3"] < threshold]
            high_gain = df[df["recent_gain3"] >= threshold]
            if len(low_gain) > 2 and len(high_gain) > 2:
                low_wr = low_gain["is_win"].mean()
                high_wr = high_gain["is_win"].mean()
                if low_wr > high_wr + 0.1:
                    suggestions.append(
                        f"  - 近3日涨幅 < {threshold}% 时胜率 {low_wr:.1%} "
                        f"vs >= {threshold}% 时 {high_wr:.1%}，考虑收紧追高保护"
                    )
                    break

    # 5. 分析 ADX
    if "adx" in df.columns:
        high_adx = df[df["adx"] >= 25]
        low_adx = df[df["adx"] < 25]
        if len(high_adx) > 2 and len(low_adx) > 2:
            high_wr = high_adx["is_win"].mean()
            low_wr = low_adx["is_win"].mean()
            if high_wr > low_wr + 0.1:
                suggestions.append(
                    f"  - ADX >= 25 时胜率 {high_wr:.1%} vs < 25 时 {low_wr:.1%}，"
                    f"趋势确认能提高胜率"
                )
            elif low_wr > high_wr + 0.1:
                suggestions.append(
                    f"  - ADX < 25 时胜率 {low_wr:.1%} vs >= 25 时 {high_wr:.1%}，"
                    f"低 ADX 反而胜率更高（可能趋势起步阶段更好买）"
                )

    # 6. 分析多头排列
    if "is_bullish_align" in df.columns:
        bullish = df[df["is_bullish_align"]]
        non_bullish = df[~df["is_bullish_align"]]
        if len(bullish) > 2 and len(non_bullish) > 2:
            bull_wr = bullish["is_win"].mean()
            non_bull_wr = non_bullish["is_win"].mean()
            if bull_wr > non_bull_wr + 0.1:
                suggestions.append(
                    f"  - 多头排列时胜率 {bull_wr:.1%} vs 非多头排列 {non_bull_wr:.1%}，"
                    f"多头排列中买入更可靠"
                )

    # 7. 分析下影线
    if "has_lower_shadow" in df.columns:
        with_shadow = df[df["has_lower_shadow"]]
        without_shadow = df[~df["has_lower_shadow"]]
        if len(with_shadow) > 2 and len(without_shadow) > 2:
            ws_wr = with_shadow["is_win"].mean()
            wos_wr = without_shadow["is_win"].mean()
            if ws_wr > wos_wr + 0.1:
                suggestions.append(
                    f"  - 有下影线时胜率 {ws_wr:.1%} vs 无下影线 {wos_wr:.1%}，"
                    f"下影线提供支撑确认"
                )

    # 8. 分析买入信号类型
    if "buy_reason" in df.columns:
        reason_stats = df.groupby("buy_reason")["is_win"].mean()
        best_reason = reason_stats.idxmax()
        worst_reason = reason_stats.idxmin()
        if reason_stats[best_reason] > reason_stats[worst_reason] + 0.15:
            suggestions.append(
                f"  - 买入信号 '{best_reason}' 胜率 {reason_stats[best_reason]:.1%} "
                f"vs '{worst_reason}' {reason_stats[worst_reason]:.1%}，"
                f"可考虑调整低胜率信号的参数或过滤条件"
            )

    if suggestions:
        for s in suggestions:
            print(s)
    else:
        print("  未发现明显的单一特征区分度，建议组合多特征分析。")

    # 输出原始交易明细供进一步分析
    print(f"\n{'=' * 90}")
    print("全部买入交易明细")
    print(f"{'=' * 90}")
    print(
        f"{'股票':<8} {'类型':<10} {'买入日':>10} {'信号':<14} {'买入价':>8} {'卖出价':>8} "
        f"{'盈亏%':>8} {'胜':>4} {'实体%':>7} {'反转力':>7} {'量比':>6} {'ADX':>6} {'近3日%':>7}"
    )
    print("-" * 110)
    for _, row in df.sort_values("profit_pct", ascending=False).iterrows():
        print(
            f"{row['stock']:<8} {row['stock_type']:<10} {row['buy_date']:>10} "
            f"{row['buy_reason']:<14} {row['buy_price']:>8.2f} {row['sell_price']:>8.2f} "
            f"{row['profit_pct']:>8.2f} {'✓' if row['is_win'] else '✗':>4} "
            f"{row['body_pct']:>7.2f} {row['reversal_ratio']:>7.2f} "
            f"{row['vol_ratio']:>6.2f} {row['adx']:>6.1f} {row['recent_gain3']:>7.2f}"
        )


def main() -> None:
    print("=" * 90)
    print("买入阳线特征提取与统计分析")
    print(f"策略: {STRATEGY_FILE.name} | K线: {KLINE_COUNT} | 股票: {len(STOCK_POOL)} 只")
    print("=" * 90)

    # 加载策略
    strategy_cls = _load_strategy_class(STRATEGY_FILE)
    print(f"策略类: {strategy_cls.__name__}")

    # 连接数据源
    print("\n正在连接行情服务器...")
    client = MacClient.from_best_host()
    client.connect()

    all_features: list[TradeFeatures] = []

    try:
        print(f"\n{'=' * 90}")
        print("回测结果摘要")
        print(f"{'=' * 90}")

        for code, market_str, stock_type in STOCK_POOL:
            try:
                df, result = _run_backtest_for_stock(
                    client, code, market_str, stock_type, strategy_cls
                )
                _print_stock_summary(code, stock_type, result)

                # 提取交易特征
                features = _extract_all_trades(df, result, code, stock_type)
                all_features.extend(features)
            except Exception as e:
                print(f"  {code} ({stock_type}): 错误 - {e}")
    finally:
        client.close()

    if not all_features:
        print("\n[!] 未提取到任何交易特征，请检查策略和股票池。")
        return

    # 转为 DataFrame 分析
    df = _features_to_dataframe(all_features)
    print(f"\n共提取 {len(df)} 笔买入交易特征")

    # 特征对比分析
    _print_feature_comparison(df)

    # 区间分布分析
    _print_distribution_analysis(df)

    # 优化建议
    _print_optimization_suggestions(df)

    print(f"\n{'=' * 90}")
    print("分析完成")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
