"""
上涨趋势识别 — 多种方法综合判断
====================================
基于图中走势特征（2026-03 至 2026-05 从 ~10.5 涨至 21.40），
使用以下方法识别上涨趋势：
  1. 均线多头排列（MA5 > MA10 > MA20 > MA60）
  2. 线性回归斜率 + R²
  3. 更高的高点和更高的低点（Higher Highs & Higher Lows）
  4. MACD 金叉 + 柱状图持续为正
  5. 综合评分

用法：
  # 使用 easy_tdx 获取真实数据
  python examples/25_trend_detect/detect_uptrend.py --market SZ --code 000001

  # 使用模拟数据演示
  python examples/25_trend_detect/detect_uptrend.py --demo
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class TrendSegment:
    """一段被识别出的趋势。"""

    start_idx: int
    end_idx: int
    direction: str  # "UP" / "DOWN" / "SIDEWAYS"
    start_price: float
    end_price: float
    change_pct: float  # 涨跌幅 %
    slope: float  # 每日平均涨幅
    r_squared: float  # 线性拟合优度
    score: float  # 综合评分 0-100


# ─────────────────────────────────────────────────────────────────────────
# 方法 1: 均线多头排列
# ─────────────────────────────────────────────────────────────────────────
def ma_bullish_alignment(df: pd.DataFrame) -> pd.Series:
    """
    判断 MA5 > MA10 > MA20 > MA60 的多头排列。
    返回布尔 Series，True 表示当日处于多头排列。
    """
    ma5 = df["close"].rolling(5).mean()
    ma10 = df["close"].rolling(10).mean()
    ma20 = df["close"].rolling(20).mean()
    ma60 = df["close"].rolling(60).mean()

    bullish = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    return bullish


def ma_slope_positive(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """MA20 斜率为正（最近 window 日内 MA20 持续上升）。"""
    ma20 = df["close"].rolling(20).mean()
    slope = ma20.diff(window)
    return slope > 0


# ─────────────────────────────────────────────────────────────────────────
# 方法 2: 线性回归斜率 + R²
# ─────────────────────────────────────────────────────────────────────────
def rolling_regression(
    close: pd.Series, window: int = 30
) -> tuple[pd.Series, pd.Series]:
    """
    滚动线性回归，返回 (斜率, R²)。
    斜率 > 0 且 R² > 0.6 视为显著上涨趋势。
    """
    n = len(close)
    slopes = np.full(n, np.nan)
    r_squares = np.full(n, np.nan)

    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    values = close.values
    for i in range(window - 1, n):
        y = values[i - window + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / x_var
        intercept = y_mean - slope * x_mean
        y_pred = slope * x + intercept
        ss_res = ((y - y_pred) ** 2).sum()
        ss_tot = ((y - y_mean) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        slopes[i] = slope
        r_squares[i] = r2

    return pd.Series(slopes, index=close.index), pd.Series(r_squares, index=close.index)


# ─────────────────────────────────────────────────────────────────────────
# 方法 3: 更高的高点 & 更高的低点
# ─────────────────────────────────────────────────────────────────────────
def higher_highs_higher_lows(
    df: pd.DataFrame, lookback: int = 5
) -> pd.Series:
    """
    识别局部高点/低点，判断是否形成 HH + HL 结构。
    lookback: 局部极值的左右窗口大小。
    当连续出现 Higher High 且对应期间低点也抬高时，标记为上涨趋势。
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    result = np.zeros(n, dtype=bool)

    # 找局部高点和低点
    local_highs: list[tuple[int, float]] = []
    local_lows: list[tuple[int, float]] = []
    for i in range(lookback, n - lookback):
        window_slice = highs[i - lookback : i + lookback + 1]
        if highs[i] == max(window_slice):
            local_highs.append((i, highs[i]))
        window_slice_l = lows[i - lookback : i + lookback + 1]
        if lows[i] == min(window_slice_l):
            local_lows.append((i, lows[i]))

    # 找到连续 Higher High 的区间
    hh_ranges: list[tuple[int, int]] = []
    for j in range(1, len(local_highs)):
        if local_highs[j][1] > local_highs[j - 1][1]:
            hh_ranges.append((local_highs[j - 1][0], local_highs[j][0]))

    # 找到连续 Higher Low 的区间
    hl_ranges: list[tuple[int, int]] = []
    for j in range(1, len(local_lows)):
        if local_lows[j][1] > local_lows[j - 1][1]:
            hl_ranges.append((local_lows[j - 1][0], local_lows[j][0]))

    # HH 和 HL 时间区间有重叠的部分 → 确认为上涨趋势
    for hh_start, hh_end in hh_ranges:
        for hl_start, hl_end in hl_ranges:
            # 计算重叠区间
            overlap_start = max(hh_start, hl_start)
            overlap_end = min(hh_end, hl_end)
            if overlap_start <= overlap_end:
                # 扩展为完整覆盖区间
                seg_start = min(hh_start, hl_start)
                seg_end = max(hh_end, hl_end)
                result[seg_start : seg_end + 1] = True

    return pd.Series(result, index=df.index)


# ─────────────────────────────────────────────────────────────────────────
# 方法 4: MACD 金叉 + 柱状图
# ─────────────────────────────────────────────────────────────────────────
def macd_uptrend(df: pd.DataFrame) -> pd.Series:
    """MACD DIF > DEA 且 MACD 柱 > 0 → 上涨动能。"""
    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    # DIF 在 DEA 上方 + 柱状图为正
    return (dif > dea) & (hist > 0)


# ─────────────────────────────────────────────────────────────────────────
# 方法 5: 综合评分
# ─────────────────────────────────────────────────────────────────────────
def compute_trend_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    综合多种信号计算每日趋势评分 (0-100)。
    权重：均线排列 30% + 回归斜率 25% + HH/HL 20% + MACD 15% + 价格在MA20上方 10%
    """
    scores = pd.DataFrame(index=df.index)

    # 信号 1: 均线多头排列
    scores["ma_bull"] = ma_bullish_alignment(df).astype(float) * 30

    # 信号 2: 回归斜率
    slopes, r2 = rolling_regression(df["close"], window=30)
    reg_signal = ((slopes > 0) & (r2 > 0.5)).astype(float)
    # R² 越高得分越高
    scores["regression"] = reg_signal * r2.clip(0, 1) * 25

    # 信号 3: HH + HL
    scores["hh_hl"] = higher_highs_higher_lows(df).astype(float) * 20

    # 信号 4: MACD
    scores["macd"] = macd_uptrend(df).astype(float) * 15

    # 信号 5: 价格站上 MA20
    ma20 = df["close"].rolling(20).mean()
    scores["above_ma20"] = (df["close"] > ma20).astype(float) * 10

    scores["total"] = scores.sum(axis=1)
    return scores


# ─────────────────────────────────────────────────────────────────────────
# 趋势段提取
# ─────────────────────────────────────────────────────────────────────────
def extract_trend_segments(
    df: pd.DataFrame, scores: pd.DataFrame, threshold: float = 50
) -> list[TrendSegment]:
    """将连续评分 >= threshold 的区间提取为上涨趋势段。"""
    is_uptrend = scores["total"] >= threshold
    segments: list[TrendSegment] = []

    in_segment = False
    start = 0

    for i in range(len(is_uptrend)):
        if is_uptrend.iloc[i] and not in_segment:
            start = i
            in_segment = True
        elif not is_uptrend.iloc[i] and in_segment:
            segments.append(_build_segment(df, scores, start, i - 1))
            in_segment = False

    if in_segment:
        segments.append(_build_segment(df, scores, start, len(df) - 1))

    return segments


def _build_segment(
    df: pd.DataFrame, scores: pd.DataFrame, start: int, end: int
) -> TrendSegment:
    seg_close = df["close"].iloc[start : end + 1]
    start_price = seg_close.iloc[0]
    end_price = seg_close.iloc[-1]
    change_pct = (end_price - start_price) / start_price * 100

    # 线性回归
    x = np.arange(len(seg_close), dtype=float)
    coeffs = np.polyfit(x, seg_close.values, 1)
    slope = coeffs[0]

    y_pred = np.polyval(coeffs, x)
    ss_res = ((seg_close.values - y_pred) ** 2).sum()
    ss_tot = ((seg_close.values - seg_close.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    avg_score = scores["total"].iloc[start : end + 1].mean()

    direction = "UP" if change_pct > 2 else ("DOWN" if change_pct < -2 else "SIDEWAYS")

    return TrendSegment(
        start_idx=start,
        end_idx=end,
        direction=direction,
        start_price=round(start_price, 2),
        end_price=round(end_price, 2),
        change_pct=round(change_pct, 2),
        slope=round(slope, 4),
        r_squared=round(r2, 4),
        score=round(avg_score, 1),
    )


# ─────────────────────────────────────────────────────────────────────────
# 可视化
# ─────────────────────────────────────────────────────────────────────────
def plot_trend(df: pd.DataFrame, scores: pd.DataFrame, segments: list[TrendSegment]):
    """绘制 K 线 + 均线 + 趋势标注。"""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("需要安装 matplotlib: pip install matplotlib")
        return

    plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1, 1]})

    ax1, ax2, ax3 = axes
    x = np.arange(len(df))

    # ── 子图 1: 价格 + 均线 + 趋势区间 ──
    ax1.plot(x, df["close"], color="black", linewidth=0.8, label="Close")
    for period, color in [(5, "orange"), (10, "blue"), (20, "purple"), (60, "green")]:
        ma = df["close"].rolling(period).mean()
        ax1.plot(x, ma, linewidth=0.7, color=color, label=f"MA{period}")

    # 标注上涨趋势段
    for seg in segments:
        if seg.direction == "UP":
            ax1.axvspan(seg.start_idx, seg.end_idx, alpha=0.15, color="red",
                        label=f"上涨 +{seg.change_pct}%")
            mid = (seg.start_idx + seg.end_idx) // 2
            ax1.annotate(
                f"↑{seg.change_pct:.1f}%\nR²={seg.r_squared:.2f}",
                xy=(mid, seg.end_price),
                fontsize=9, color="red", ha="center",
                fontweight="bold",
            )

    ax1.set_ylabel("价格")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.set_title("上涨趋势识别（综合评分法）")

    # ── 子图 2: 趋势评分 ──
    colors = ["red" if s >= 50 else "gray" for s in scores["total"]]
    ax2.bar(x, scores["total"], color=colors, alpha=0.7, width=1)
    ax2.axhline(50, color="red", linestyle="--", linewidth=0.8, label="阈值=50")
    ax2.set_ylabel("趋势评分")
    ax2.legend(fontsize=8)

    # ── 子图 3: MACD ──
    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    ax3.plot(x, dif, color="blue", linewidth=0.7, label="DIF")
    ax3.plot(x, dea, color="orange", linewidth=0.7, label="DEA")
    hist_colors = ["red" if v >= 0 else "green" for v in hist]
    ax3.bar(x, hist, color=hist_colors, alpha=0.5, width=1)
    ax3.set_ylabel("MACD")
    ax3.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig("trend_detect_result.png", dpi=150, bbox_inches="tight")
    print("图表已保存: trend_detect_result.png")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────
# 模拟数据（复现图中走势）
# ─────────────────────────────────────────────────────────────────────────
def generate_demo_data() -> pd.DataFrame:
    """
    生成模拟数据，复现图中走势：
    2026-02 ~ 2026-03 横盘 (~10.5)
    2026-03 ~ 2026-05 快速上涨 (10.5 → 21.4)
    2026-05 ~ 2026-07 回落震荡 (21.4 → 11.5)
    """
    np.random.seed(42)
    dates = pd.bdate_range("2026-02-01", "2026-07-25")
    n = len(dates)

    prices = np.zeros(n)
    # 阶段 1: 横盘 (约 40 天)
    phase1_end = 40
    prices[:phase1_end] = 10.5 + np.random.normal(0, 0.15, phase1_end)

    # 阶段 2: 快速上涨 (约 50 天, 10.5 → 21.4)
    phase2_end = phase1_end + 50
    t2 = np.linspace(0, 1, phase2_end - phase1_end)
    # 使用指数增长模拟加速上涨
    prices[phase1_end:phase2_end] = 10.5 + 10.9 * (t2 ** 1.5) + np.random.normal(0, 0.2, phase2_end - phase1_end)

    # 阶段 3: 回落震荡 (剩余天数, 21.4 → 11.5)
    phase3_len = n - phase2_end
    t3 = np.linspace(0, 1, phase3_len)
    prices[phase2_end:] = 21.4 - 9.9 * (t3 ** 0.8) + np.random.normal(0, 0.3, phase3_len)

    # 构建 OHLCV
    close = prices
    open_ = close + np.random.normal(0, 0.1, n)
    high = np.maximum(open_, close) + np.abs(np.random.normal(0, 0.15, n))
    low = np.minimum(open_, close) - np.abs(np.random.normal(0, 0.15, n))
    vol = np.random.uniform(5e6, 2e7, n)
    # 上涨阶段放量
    vol[phase1_end:phase2_end] *= 1.8

    df = pd.DataFrame({
        "open": open_,
        "close": close,
        "high": high,
        "low": low,
        "vol": vol,
    }, index=dates)

    return df


# ─────────────────────────────────────────────────────────────────────────
# 使用 easy_tdx 获取真实数据
# ─────────────────────────────────────────────────────────────────────────
def fetch_real_data(market: str, code: str, count: int = 250) -> pd.DataFrame:
    """通过 easy_tdx MAC 客户端获取日 K 数据。"""
    sys.path.insert(0, "/Users/a1-6/project/easy_tdx/src")
    from easy_tdx.cli.conn import get_mac_client
    from easy_tdx.cli.parsers import parse_market
    from easy_tdx.mac.enums import Period

    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_stock_kline(mkt, code, period=Period.DAILY, start=0, count=count)

    # MAC 客户端返回 datetime 列 + 整数索引，转为 DatetimeIndex
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
    return df


# ─────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────
def analyze(df: pd.DataFrame, threshold: float = 50, plot: bool = True):
    """执行完整的上涨趋势识别流程。"""
    print("=" * 60)
    print("        上涨趋势识别分析报告")
    print("=" * 60)
    print(f"数据量: {len(df)} 根K线")
    print(f"时间范围: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    print()

    # 计算综合评分
    scores = compute_trend_score(df)

    # 提取趋势段
    segments = extract_trend_segments(df, scores, threshold=threshold)

    # 输出结果
    up_segments = [s for s in segments if s.direction == "UP"]
    print(f"识别到 {len(up_segments)} 段上涨趋势 (评分阈值={threshold}):")
    print("-" * 60)

    for i, seg in enumerate(up_segments, 1):
        start_date = df.index[seg.start_idx].strftime("%Y-%m-%d")
        end_date = df.index[seg.end_idx].strftime("%Y-%m-%d")
        duration = seg.end_idx - seg.start_idx + 1
        print(f"  [{i}] {start_date} → {end_date} ({duration}天)")
        print(f"      价格: {seg.start_price} → {seg.end_price}")
        print(f"      涨幅: +{seg.change_pct}%")
        print(f"      日均斜率: {seg.slope}")
        print(f"      R²: {seg.r_squared}")
        print(f"      综合评分: {seg.score}")
        print()

    # 各信号统计
    print("─" * 60)
    print("各信号触发天数统计:")
    ma_bull = ma_bullish_alignment(df)
    macd_up = macd_uptrend(df)
    hh_hl = higher_highs_higher_lows(df)
    slopes, r2 = rolling_regression(df["close"], 30)
    reg_sig = (slopes > 0) & (r2 > 0.5)

    total_days = len(df)
    print(f"  均线多头排列: {ma_bull.sum()}/{total_days} 天 ({ma_bull.sum()/total_days*100:.1f}%)")
    print(f"  回归斜率显著: {reg_sig.sum()}/{total_days} 天 ({reg_sig.sum()/total_days*100:.1f}%)")
    print(f"  HH+HL 结构:  {hh_hl.sum()}/{total_days} 天 ({hh_hl.sum()/total_days*100:.1f}%)")
    print(f"  MACD 多头:   {macd_up.sum()}/{total_days} 天 ({macd_up.sum()/total_days*100:.1f}%)")
    print("=" * 60)

    # 绘图
    if plot:
        plot_trend(df, scores, segments)

    return segments, scores


def main():
    parser = argparse.ArgumentParser(description="上涨趋势识别")
    parser.add_argument("--market", default="SZ", help="市场 (SZ/SH)")
    parser.add_argument("--code", default="000001", help="股票代码")
    parser.add_argument("--count", type=int, default=250, help="K线数量")
    parser.add_argument("--threshold", type=float, default=50, help="趋势评分阈值")
    parser.add_argument("--demo", action="store_true", help="使用模拟数据")
    parser.add_argument("--no-plot", action="store_true", help="不绘图")
    args = parser.parse_args()

    if args.demo:
        print("使用模拟数据（复现图中走势）...")
        df = generate_demo_data()
    else:
        print(f"获取 {args.market} {args.code} 日K数据...")
        df = fetch_real_data(args.market, args.code, args.count)

    analyze(df, threshold=args.threshold, plot=not args.no_plot)


if __name__ == "__main__":
    main()
