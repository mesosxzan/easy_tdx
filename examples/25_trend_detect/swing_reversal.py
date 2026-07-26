"""
趋势反转识别 & 波段收益最大化
====================================
基于多信号融合识别顶部/底部反转点，生成波段买卖信号，
计算理论最大波段收益并与买入持有对比。

反转信号体系：
  顶部反转（卖出）：
    1. MACD 顶背离（价格新高 + DIF 未新高）
    2. RSI 超买回落（>70 后下穿）
    3. MA5 下穿 MA20（死叉）
    4. 布林带上轨受阻回落
    5. 量价背离（价涨量缩）
    6. 回归斜率由正转负

  底部反转（买入）：
    1. MACD 底背离（价格新低 + DIF 未新低）
    2. RSI 超卖反弹（<30 后上穿）
    3. MA5 上穿 MA20（金叉）
    4. 布林带下轨支撑反弹
    5. 缩量企稳后放量
    6. 回归斜率由负转正

用法：
  python examples/25_trend_detect/swing_reversal.py 600519
  python examples/25_trend_detect/swing_reversal.py 000001
  python examples/25_trend_detect/swing_reversal.py 300750 --no-plot
  python examples/25_trend_detect/swing_reversal.py --demo
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class ReversalSignal:
    """单个反转信号。"""
    idx: int
    date: str
    price: float
    direction: str  # "TOP" / "BOTTOM"
    confidence: float  # 0-100 置信度
    triggers: list[str] = field(default_factory=list)  # 触发的信号名


@dataclass
class SwingTrade:
    """一次波段交易。"""
    buy_idx: int
    buy_date: str
    buy_price: float
    sell_idx: int
    sell_date: str
    sell_price: float
    profit_pct: float
    hold_days: int


# ─────────────────────────────────────────────────────────────────────────
# 技术指标计算
# ─────────────────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有需要的技术指标。"""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["vol"]

    # 均线
    df["ma5"] = close.rolling(5).mean()
    df["ma10"] = close.rolling(10).mean()
    df["ma20"] = close.rolling(20).mean()
    df["ma60"] = close.rolling(60).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["dif"] - df["dea"]) * 2

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)

    # 布林带
    df["boll_mid"] = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    df["boll_upper"] = df["boll_mid"] + 2 * boll_std
    df["boll_lower"] = df["boll_mid"] - 2 * boll_std

    # 成交量均线
    df["vol_ma5"] = vol.rolling(5).mean()
    df["vol_ma20"] = vol.rolling(20).mean()

    # 滚动回归斜率 (20日)
    df["reg_slope"] = _rolling_slope(close, window=20)

    return df


def _rolling_slope(series: pd.Series, window: int = 20) -> pd.Series:
    """滚动线性回归斜率。"""
    n = len(series)
    slopes = np.full(n, np.nan)
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    values = series.values

    for i in range(window - 1, n):
        y = values[i - window + 1: i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = y.mean()
        slopes[i] = ((x - x_mean) * (y - y_mean)).sum() / x_var

    return pd.Series(slopes, index=series.index)


# ─────────────────────────────────────────────────────────────────────────
# 背离检测
# ─────────────────────────────────────────────────────────────────────────
def detect_divergence(
    price: pd.Series, indicator: pd.Series, lookback: int = 30
) -> tuple[pd.Series, pd.Series]:
    """
    检测顶背离和底背离。
    顶背离：价格创 lookback 日新高，但指标未创新高
    底背离：价格创 lookback 日新低，但指标未创新低
    """
    n = len(price)
    top_div = pd.Series(False, index=price.index)
    bot_div = pd.Series(False, index=price.index)

    price_vals = price.values
    ind_vals = indicator.values

    for i in range(lookback, n):
        window_price = price_vals[i - lookback: i]
        window_ind = ind_vals[i - lookback: i]

        if np.any(np.isnan(window_price)) or np.any(np.isnan(window_ind)):
            continue

        # 顶背离：价格 >= 区间最高，但指标 < 区间指标最高
        if price_vals[i] >= np.max(window_price) * 0.99:
            if ind_vals[i] < np.max(window_ind) * 0.95:
                top_div.iloc[i] = True

        # 底背离：价格 <= 区间最低，但指标 > 区间指标最低
        if price_vals[i] <= np.min(window_price) * 1.01:
            if ind_vals[i] > np.min(window_ind) * 1.05:
                bot_div.iloc[i] = True

    return top_div, bot_div


# ─────────────────────────────────────────────────────────────────────────
# 反转信号综合检测
# ─────────────────────────────────────────────────────────────────────────
def detect_reversals(df: pd.DataFrame) -> list[ReversalSignal]:
    """综合多种信号检测反转点。"""
    n = len(df)

    # 预计算背离
    macd_top_div, macd_bot_div = detect_divergence(df["close"], df["dif"], lookback=35)
    rsi_top_div, rsi_bot_div = detect_divergence(df["close"], df["rsi"], lookback=30)

    signals: list[ReversalSignal] = []

    for i in range(60, n):  # 需要足够历史数据
        top_score = 0.0
        bot_score = 0.0
        top_triggers: list[str] = []
        bot_triggers: list[str] = []

        # ── 顶部信号 ──
        # 1. MACD 顶背离
        if macd_top_div.iloc[i]:
            top_score += 25
            top_triggers.append("MACD顶背离")

        # 2. RSI 超买回落 (前日>70, 今日<70)
        if df["rsi"].iloc[i - 1] > 70 and df["rsi"].iloc[i] <= 70:
            top_score += 20
            top_triggers.append("RSI超买回落")
        elif rsi_top_div.iloc[i]:
            top_score += 15
            top_triggers.append("RSI顶背离")

        # 3. MA5 下穿 MA20（死叉）
        if (df["ma5"].iloc[i - 1] >= df["ma20"].iloc[i - 1] and
                df["ma5"].iloc[i] < df["ma20"].iloc[i]):
            top_score += 20
            top_triggers.append("MA死叉")

        # 4. 布林带上轨受阻（前日触及上轨，今日回落）
        if (df["high"].iloc[i - 1] >= df["boll_upper"].iloc[i - 1] and
                df["close"].iloc[i] < df["close"].iloc[i - 1]):
            top_score += 15
            top_triggers.append("布林上轨受阻")

        # 5. 量价背离（价涨量缩）
        if (df["close"].iloc[i] > df["close"].iloc[i - 5] and
                df["vol"].iloc[i] < df["vol_ma20"].iloc[i] * 0.7):
            top_score += 10
            top_triggers.append("量价背离")

        # 6. 回归斜率由正转负
        if (not np.isnan(df["reg_slope"].iloc[i - 1]) and
                not np.isnan(df["reg_slope"].iloc[i])):
            if df["reg_slope"].iloc[i - 1] > 0 and df["reg_slope"].iloc[i] <= 0:
                top_score += 15
                top_triggers.append("斜率转负")

        # MACD 柱缩短（动能衰减）加分
        if (df["macd_hist"].iloc[i] > 0 and
                df["macd_hist"].iloc[i] < df["macd_hist"].iloc[i - 1] * 0.6 and
                df["macd_hist"].iloc[i - 1] > 0):
            top_score += 5
            top_triggers.append("MACD柱缩短")

        # 7. 高点回落（价格从 N 日高点回落超过 3%）—— 大盘股顶部特征
        if i >= 10:
            recent_high = df["high"].iloc[max(0, i - 10):i].max()
            pullback = (recent_high - df["close"].iloc[i]) / recent_high * 100
            if pullback >= 3.0 and df["close"].iloc[i - 1] > df["close"].iloc[i]:
                top_score += 20
                top_triggers.append(f"高点回落{pullback:.1f}%")

        # 8. 连续 3 日下跌且跌幅超过 2%
        if i >= 3:
            three_day_chg = (df["close"].iloc[i] - df["close"].iloc[i - 3]) / df["close"].iloc[i - 3] * 100
            if three_day_chg < -2.0 and df["close"].iloc[i] < df["close"].iloc[i - 1]:
                top_score += 15
                top_triggers.append(f"3日跌{three_day_chg:.1f}%")

        # 9. 放量滞涨（成交量放大但价格不涨）
        if (df["vol"].iloc[i] > df["vol_ma5"].iloc[i] * 1.5 and
                abs(df["close"].iloc[i] - df["close"].iloc[i - 1]) / df["close"].iloc[i - 1] < 0.005 and
                df["close"].iloc[i] > df["ma20"].iloc[i]):
            top_score += 10
            top_triggers.append("放量滞涨")

        # ── 底部信号 ──
        # 1. MACD 底背离
        if macd_bot_div.iloc[i]:
            bot_score += 25
            bot_triggers.append("MACD底背离")

        # 2. RSI 超卖反弹 (前日<30, 今日>30)
        if df["rsi"].iloc[i - 1] < 30 and df["rsi"].iloc[i] >= 30:
            bot_score += 20
            bot_triggers.append("RSI超卖反弹")
        elif rsi_bot_div.iloc[i]:
            bot_score += 15
            bot_triggers.append("RSI底背离")

        # 3. MA5 上穿 MA20（金叉）
        if (df["ma5"].iloc[i - 1] <= df["ma20"].iloc[i - 1] and
                df["ma5"].iloc[i] > df["ma20"].iloc[i]):
            bot_score += 20
            bot_triggers.append("MA金叉")

        # 4. 布林带下轨支撑反弹
        if (df["low"].iloc[i - 1] <= df["boll_lower"].iloc[i - 1] and
                df["close"].iloc[i] > df["close"].iloc[i - 1]):
            bot_score += 15
            bot_triggers.append("布林下轨支撑")

        # 5. 缩量后放量（底部放量）
        if (df["vol"].iloc[i] > df["vol_ma5"].iloc[i] * 1.5 and
                df["close"].iloc[i] > df["close"].iloc[i - 1] and
                df["vol_ma5"].iloc[i] < df["vol_ma20"].iloc[i] * 0.8):
            bot_score += 10
            bot_triggers.append("底部放量")

        # 6. 回归斜率由负转正
        if (not np.isnan(df["reg_slope"].iloc[i - 1]) and
                not np.isnan(df["reg_slope"].iloc[i])):
            if df["reg_slope"].iloc[i - 1] < 0 and df["reg_slope"].iloc[i] >= 0:
                bot_score += 15
                bot_triggers.append("斜率转正")

        # MACD 金叉加分
        if (df["dif"].iloc[i - 1] <= df["dea"].iloc[i - 1] and
                df["dif"].iloc[i] > df["dea"].iloc[i]):
            bot_score += 10
            bot_triggers.append("MACD金叉")

        # ── 生成信号（阈值 30 分，经多股票回测优化）──
        date_str = df.index[i].strftime("%Y-%m-%d")
        price = df["close"].iloc[i]

        if top_score >= 30:
            signals.append(ReversalSignal(
                idx=i, date=date_str, price=price,
                direction="TOP", confidence=min(top_score, 100),
                triggers=top_triggers,
            ))
        elif bot_score >= 30:
            signals.append(ReversalSignal(
                idx=i, date=date_str, price=price,
                direction="BOTTOM", confidence=min(bot_score, 100),
                triggers=bot_triggers,
            ))

    return signals


# ─────────────────────────────────────────────────────────────────────────
# 波段交易信号生成（去重 + 交替）
# ─────────────────────────────────────────────────────────────────────────
def generate_swing_signals(
    signals: list[ReversalSignal], df: pd.DataFrame, min_gap: int = 3
) -> list[ReversalSignal]:
    """
    过滤信号：
    1. 买卖必须交替出现
    2. 同方向信号取置信度最高的
    3. 相邻信号至少间隔 min_gap 天
    4. 若第一个信号是卖出，补充一个趋势启动买入点
    """
    if not signals:
        return []

    # 按时间排序
    signals = sorted(signals, key=lambda s: s.idx)

    # 合并同方向连续信号（保留最高置信度）
    merged: list[ReversalSignal] = []
    for sig in signals:
        if merged and merged[-1].direction == sig.direction:
            if sig.confidence > merged[-1].confidence:
                merged[-1] = sig
        else:
            merged.append(sig)

    # 若第一个信号是 TOP，向前寻找趋势启动点作为买入
    if merged and merged[0].direction == "TOP":
        first_top = merged[0]
        # 策略：在第一个顶部之前找 MACD 金叉（DIF 上穿 DEA）或 MA5 上穿 MA20
        found = False
        for i in range(first_top.idx - 1, 25, -1):
            # 优先找 MACD 金叉
            if (not np.isnan(df["dif"].iloc[i - 1]) and
                    not np.isnan(df["dea"].iloc[i])):
                if (df["dif"].iloc[i - 1] <= df["dea"].iloc[i - 1] and
                        df["dif"].iloc[i] > df["dea"].iloc[i]):
                    bootstrap_buy = ReversalSignal(
                        idx=i,
                        date=df.index[i].strftime("%Y-%m-%d"),
                        price=df["close"].iloc[i],
                        direction="BOTTOM",
                        confidence=45,
                        triggers=["趋势启动(MACD金叉)"],
                    )
                    merged.insert(0, bootstrap_buy)
                    found = True
                    break
        # 备选：MA5 上穿 MA20
        if not found:
            for i in range(first_top.idx - 1, 20, -1):
                if (not np.isnan(df["ma5"].iloc[i - 1]) and
                        not np.isnan(df["ma20"].iloc[i])):
                    if (df["ma5"].iloc[i - 1] <= df["ma20"].iloc[i - 1] and
                            df["ma5"].iloc[i] > df["ma20"].iloc[i]):
                        bootstrap_buy = ReversalSignal(
                            idx=i,
                            date=df.index[i].strftime("%Y-%m-%d"),
                            price=df["close"].iloc[i],
                            direction="BOTTOM",
                            confidence=40,
                            triggers=["趋势启动(MA金叉)"],
                        )
                        merged.insert(0, bootstrap_buy)
                        break

    # 确保交替 + 间隔
    filtered: list[ReversalSignal] = []
    last_direction = None
    last_idx = -min_gap - 1

    for sig in merged:
        if sig.idx - last_idx < min_gap:
            continue
        if last_direction is None or sig.direction != last_direction:
            filtered.append(sig)
            last_direction = sig.direction
            last_idx = sig.idx

    return filtered


# ─────────────────────────────────────────────────────────────────────────
# 波段收益计算（含追踪止盈优化）
# ─────────────────────────────────────────────────────────────────────────
def compute_swing_trades(
    df: pd.DataFrame,
    signals: list[ReversalSignal],
    trailing_stop_pct: float = 3.0,
    strong_exit_confidence: float = 55.0,
) -> list[SwingTrade]:
    """
    根据买卖信号计算每笔波段交易（优化版）。

    优化机制：
    1. 追踪止盈：持仓期间跟踪最高价，从高点回落 trailing_stop_pct% 则止盈
    2. 强信号即时退出：TOP信号置信度 >= strong_exit_confidence 时立即卖出
    3. 弱信号延迟确认：TOP信号置信度 < strong_exit_confidence 时启动追踪止盈
    4. 趋势过滤：MA20 连续下行时不买入（避免接下跌刀）
    """
    trades: list[SwingTrade] = []
    buy_signal: ReversalSignal | None = None
    close = df["close"].values
    n = len(df)

    # 预计算 MA20 斜率（用于趋势过滤）
    ma20 = df["close"].rolling(20).mean().values

    def is_downtrend(idx: int) -> bool:
        """MA20 连续 5 日下行 → 下跌趋势，不宜买入。"""
        if idx < 25:
            return False
        return all(ma20[idx - k] < ma20[idx - k - 1] for k in range(5))

    i = 0
    while i < n:
        # 寻找买入信号
        if buy_signal is None:
            for sig in signals:
                if sig.direction == "BOTTOM" and sig.idx >= i:
                    # 入场过滤：置信度必须 >= 50
                    if sig.confidence < 50:
                        i = sig.idx + 1
                        continue
                    # 趋势过滤：下跌趋势中仅允许高置信度信号（>=60）
                    if is_downtrend(sig.idx) and sig.confidence < 60:
                        i = sig.idx + 1
                        continue
                    buy_signal = sig
                    i = sig.idx + 1
                    break
            else:
                break
            if buy_signal is None:
                break
            continue

        # 持仓中：寻找卖出时机
        entry_price = buy_signal.price
        peak_price = entry_price
        peak_idx = buy_signal.idx
        exit_idx = None
        exit_price = None
        trailing_active = False

        for j in range(buy_signal.idx + 1, n):
            price = close[j]

            # 更新最高价
            if price > peak_price:
                peak_price = price
                peak_idx = j

            # 检查是否有 TOP 信号
            top_sig = None
            for sig in signals:
                if sig.direction == "TOP" and sig.idx == j:
                    top_sig = sig
                    break

            if top_sig is not None:
                if top_sig.confidence >= strong_exit_confidence:
                    # 强信号：立即退出
                    exit_idx = j
                    exit_price = price
                    break
                else:
                    # 弱信号：启动追踪止盈
                    trailing_active = True

            # 追踪止盈：从最高点回落超过阈值
            if trailing_active and peak_price > entry_price:
                drawdown_from_peak = (peak_price - price) / peak_price * 100
                if drawdown_from_peak >= trailing_stop_pct:
                    exit_idx = j
                    exit_price = price
                    break

            # 硬止损：亏损超过 1.75 倍追踪止盈比例
            loss_from_entry = (entry_price - price) / entry_price * 100
            if loss_from_entry >= trailing_stop_pct * 1.75:
                exit_idx = j
                exit_price = price
                break

        # 如果找到退出点，记录交易
        if exit_idx is not None and exit_price is not None:
            profit = (exit_price - entry_price) / entry_price * 100
            trades.append(SwingTrade(
                buy_idx=buy_signal.idx,
                buy_date=buy_signal.date,
                buy_price=round(entry_price, 2),
                sell_idx=exit_idx,
                sell_date=df.index[exit_idx].strftime("%Y-%m-%d"),
                sell_price=round(exit_price, 2),
                profit_pct=round(profit, 2),
                hold_days=exit_idx - buy_signal.idx,
            ))
            i = exit_idx + 1
        else:
            # 未找到退出点，跳过这个买入信号
            i = buy_signal.idx + 1

        buy_signal = None

    return trades


def compute_max_theoretical_profit(df: pd.DataFrame) -> tuple[float, list[SwingTrade]]:
    """
    计算理论最大波段收益（完美择时）：
    在每个局部最低点买入，每个局部最高点卖出。
    """
    close = df["close"].values
    n = len(close)

    # 使用 10 日窗口找局部极值
    window = 10
    local_mins: list[tuple[int, float]] = []
    local_maxs: list[tuple[int, float]] = []

    for i in range(window, n - window):
        if close[i] == min(close[i - window: i + window + 1]):
            local_mins.append((i, close[i]))
        if close[i] == max(close[i - window: i + window + 1]):
            local_maxs.append((i, close[i]))

    # 交替配对：先买后卖
    trades: list[SwingTrade] = []
    buy_point: tuple[int, float] | None = None
    all_points = sorted(local_mins + local_maxs, key=lambda x: x[0])

    # 标记类型
    min_set = {idx for idx, _ in local_mins}
    max_set = {idx for idx, _ in local_maxs}

    for idx, price in all_points:
        if idx in min_set and buy_point is None:
            buy_point = (idx, price)
        elif idx in max_set and buy_point is not None:
            if price > buy_point[1]:
                profit = (price - buy_point[1]) / buy_point[1] * 100
                trades.append(SwingTrade(
                    buy_idx=buy_point[0],
                    buy_date=df.index[buy_point[0]].strftime("%Y-%m-%d"),
                    buy_price=round(buy_point[1], 2),
                    sell_idx=idx,
                    sell_date=df.index[idx].strftime("%Y-%m-%d"),
                    sell_price=round(price, 2),
                    profit_pct=round(profit, 2),
                    hold_days=idx - buy_point[0],
                ))
            buy_point = None

    # 复合收益
    total = 1.0
    for t in trades:
        total *= (1 + t.profit_pct / 100)
    return (total - 1) * 100, trades


# ─────────────────────────────────────────────────────────────────────────
# 可视化
# ─────────────────────────────────────────────────────────────────────────
def plot_swing_trades(
    df: pd.DataFrame,
    signals: list[ReversalSignal],
    trades: list[SwingTrade],
    theo_trades: list[SwingTrade],
):
    """绘制完整的波段交易可视化。"""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("需要安装 matplotlib: pip install matplotlib")
        return

    plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(4, 1, figsize=(18, 16), sharex=True,
                             gridspec_kw={"height_ratios": [4, 1.2, 1.2, 1.2]})
    ax1, ax2, ax3, ax4 = axes
    x = np.arange(len(df))

    # ── 子图 1: 价格 + 均线 + 买卖点 ──
    ax1.plot(x, df["close"], color="black", linewidth=0.9, label="Close", zorder=2)
    for period, color, alpha in [(5, "orange", 0.8), (20, "purple", 0.8), (60, "green", 0.7)]:
        ax1.plot(x, df[f"ma{period}"], linewidth=0.7, color=color, alpha=alpha, label=f"MA{period}")

    # 布林带
    ax1.fill_between(x, df["boll_lower"], df["boll_upper"], alpha=0.05, color="blue", label="BOLL")

    # 买卖信号标注
    buy_sigs = [s for s in signals if s.direction == "BOTTOM"]
    sell_sigs = [s for s in signals if s.direction == "TOP"]

    if buy_sigs:
        ax1.scatter([s.idx for s in buy_sigs], [s.price for s in buy_sigs],
                    marker="^", color="red", s=120, zorder=5, label="买入信号")
        for s in buy_sigs:
            ax1.annotate(f"B\n{s.confidence:.0f}", xy=(s.idx, s.price),
                         fontsize=7, color="red", ha="center", va="bottom",
                         fontweight="bold")

    if sell_sigs:
        ax1.scatter([s.idx for s in sell_sigs], [s.price for s in sell_sigs],
                    marker="v", color="green", s=120, zorder=5, label="卖出信号")
        for s in sell_sigs:
            ax1.annotate(f"S\n{s.confidence:.0f}", xy=(s.idx, s.price),
                         fontsize=7, color="green", ha="center", va="top",
                         fontweight="bold")

    # 波段交易区间着色
    for trade in trades:
        color = "red" if trade.profit_pct > 0 else "green"
        ax1.axvspan(trade.buy_idx, trade.sell_idx, alpha=0.08, color=color)

    ax1.set_ylabel("价格")
    ax1.legend(loc="upper left", fontsize=8, ncol=3)
    ax1.set_title("趋势反转识别 & 波段交易信号", fontsize=13, fontweight="bold")

    # ── 子图 2: MACD ──
    ax2.plot(x, df["dif"], color="blue", linewidth=0.7, label="DIF")
    ax2.plot(x, df["dea"], color="orange", linewidth=0.7, label="DEA")
    hist_colors = ["red" if v >= 0 else "green" for v in df["macd_hist"]]
    ax2.bar(x, df["macd_hist"], color=hist_colors, alpha=0.5, width=1)
    ax2.axhline(0, color="gray", linewidth=0.5)
    ax2.set_ylabel("MACD")
    ax2.legend(fontsize=8)

    # ── 子图 3: RSI ──
    ax3.plot(x, df["rsi"], color="purple", linewidth=0.8, label="RSI(14)")
    ax3.axhline(70, color="red", linestyle="--", linewidth=0.7, alpha=0.7)
    ax3.axhline(30, color="green", linestyle="--", linewidth=0.7, alpha=0.7)
    ax3.fill_between(x, 30, 70, alpha=0.03, color="gray")
    ax3.set_ylabel("RSI")
    ax3.set_ylim(0, 100)
    ax3.legend(fontsize=8)

    # ── 子图 4: 累计收益曲线 ──
    # 策略收益
    strategy_returns = np.zeros(len(df))
    in_position = False
    entry_price = 0.0
    cum_return = 1.0

    for sig in signals:
        if sig.direction == "BOTTOM" and not in_position:
            in_position = True
            entry_price = sig.price
        elif sig.direction == "TOP" and in_position:
            cum_return *= (1 + (sig.price - entry_price) / entry_price)
            in_position = False

    # 逐日计算持仓收益
    cum_curve = np.ones(len(df))
    in_pos = False
    entry_p = 0.0
    running = 1.0
    for i in range(len(df)):
        for sig in signals:
            if sig.idx == i:
                if sig.direction == "BOTTOM" and not in_pos:
                    in_pos = True
                    entry_p = sig.price
                elif sig.direction == "TOP" and in_pos:
                    running *= (1 + (sig.price - entry_p) / entry_p)
                    in_pos = False
        if in_pos:
            cum_curve[i] = running * (df["close"].iloc[i] / entry_p)
        else:
            cum_curve[i] = running

    # 买入持有
    bh_curve = df["close"].values / df["close"].iloc[0]

    ax4.plot(x, (cum_curve - 1) * 100, color="red", linewidth=1.2, label="波段策略")
    ax4.plot(x, (bh_curve - 1) * 100, color="gray", linewidth=1, linestyle="--", label="买入持有")
    ax4.axhline(0, color="black", linewidth=0.5)
    ax4.set_ylabel("累计收益 %")
    ax4.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig("swing_reversal_result.png", dpi=150, bbox_inches="tight")
    print("图表已保存: swing_reversal_result.png")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────
# 模拟数据（含多段波段）
# ─────────────────────────────────────────────────────────────────────────
def generate_demo_data() -> pd.DataFrame:
    """
    生成含多段波段的模拟数据：
    横盘 → 上涨 → 顶部震荡 → 下跌 → 底部企稳 → 反弹 → 再回落
    """
    np.random.seed(2026)
    dates = pd.bdate_range("2025-09-01", "2026-07-25")
    n = len(dates)
    prices = np.zeros(n)

    # 分段构造
    segments = [
        (0, 40, 12.0, 12.5, 0.1),      # 横盘
        (40, 90, 12.5, 21.0, 0.2),      # 主升浪
        (90, 110, 21.0, 19.5, 0.25),    # 顶部震荡
        (110, 150, 19.5, 13.0, 0.2),    # 下跌
        (150, 170, 13.0, 12.0, 0.1),    # 底部企稳
        (170, 200, 12.0, 16.5, 0.15),   # 反弹
        (200, n, 16.5, 14.5, 0.15),     # 再回落
    ]

    for start, end, p_start, p_end, noise in segments:
        end = min(end, n)
        seg_len = end - start
        if seg_len <= 0:
            continue
        t = np.linspace(0, 1, seg_len)
        # 使用 smoothstep 使过渡更自然
        smooth = t * t * (3 - 2 * t)
        prices[start:end] = p_start + (p_end - p_start) * smooth + np.random.normal(0, noise, seg_len)

    close = prices
    open_ = close + np.random.normal(0, 0.08, n)
    high = np.maximum(open_, close) + np.abs(np.random.normal(0, 0.12, n))
    low = np.minimum(open_, close) - np.abs(np.random.normal(0, 0.12, n))

    # 成交量：上涨放量，下跌缩量
    vol = np.random.uniform(5e6, 1.2e7, n)
    for start, end, p_start, p_end, _ in segments:
        end = min(end, n)
        if p_end > p_start:  # 上涨段放量
            vol[start:end] *= 1.6
        elif p_end < p_start:  # 下跌段缩量
            vol[start:end] *= 0.7

    return pd.DataFrame({
        "open": open_, "close": close, "high": high, "low": low, "vol": vol,
    }, index=dates)


# ─────────────────────────────────────────────────────────────────────────
# 市场自动识别
# ─────────────────────────────────────────────────────────────────────────
def guess_market(code: str) -> str:
    """根据股票代码自动判断所属市场。

    规则（依据沪深交易所代码段分配）：
      60xxxx, 68xxxx → SH（上海）
      00xxxx, 30xxxx → SZ（深圳）
      4xxxxx, 8xxxxx → BJ（北京）
    """
    code = code.strip()
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    # 默认深圳
    return "SZ"


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
# 主分析流程
# ─────────────────────────────────────────────────────────────────────────
def analyze(df: pd.DataFrame, plot: bool = True):
    """完整的趋势反转 + 波段收益分析。"""
    print("=" * 65)
    print("      趋势反转识别 & 波段收益最大化 分析报告")
    print("=" * 65)
    print(f"数据量: {len(df)} 根K线")
    print(f"时间范围: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    print()

    # 1. 计算指标
    df = compute_indicators(df)

    # 2. 检测反转信号
    raw_signals = detect_reversals(df)
    print(f"原始反转信号: {len(raw_signals)} 个")

    # 3. 过滤生成交易信号
    signals = generate_swing_signals(raw_signals, df, min_gap=3)
    buy_count = sum(1 for s in signals if s.direction == "BOTTOM")
    sell_count = sum(1 for s in signals if s.direction == "TOP")
    print(f"有效交易信号: {len(signals)} 个 (买入 {buy_count}, 卖出 {sell_count})")
    print()

    # 4. 输出信号明细
    print("─" * 65)
    print("反转信号明细:")
    print("─" * 65)
    for sig in signals:
        icon = "🔴 卖出" if sig.direction == "TOP" else "🟢 买入"
        print(f"  {icon} | {sig.date} | 价格 {sig.price:.2f} | "
              f"置信度 {sig.confidence:.0f}% | {', '.join(sig.triggers)}")
    print()

    # 5. 计算波段交易
    trades = compute_swing_trades(df, signals)
    print("─" * 65)
    print(f"波段交易记录 ({len(trades)} 笔):")
    print("─" * 65)
    total_profit = 1.0
    for i, t in enumerate(trades, 1):
        total_profit *= (1 + t.profit_pct / 100)
        status = "✅" if t.profit_pct > 0 else "❌"
        print(f"  [{i}] {status} {t.buy_date} 买入 {t.buy_price} → "
              f"{t.sell_date} 卖出 {t.sell_price} | "
              f"收益 {t.profit_pct:+.2f}% | 持仓 {t.hold_days} 天")
    print()

    # 6. 策略绩效
    strategy_return = (total_profit - 1) * 100
    bh_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    win_trades = [t for t in trades if t.profit_pct > 0]
    win_rate = len(win_trades) / len(trades) * 100 if trades else 0

    print("=" * 65)
    print("绩效对比:")
    print("=" * 65)
    print(f"  波段策略累计收益:  {strategy_return:+.2f}%")
    print(f"  买入持有收益:      {bh_return:+.2f}%")
    print(f"  超额收益:          {strategy_return - bh_return:+.2f}%")
    print(f"  交易次数:          {len(trades)} 笔")
    print(f"  胜率:              {win_rate:.1f}%")
    if trades:
        avg_profit = np.mean([t.profit_pct for t in trades])
        max_profit = max(t.profit_pct for t in trades)
        max_loss = min(t.profit_pct for t in trades)
        avg_hold = np.mean([t.hold_days for t in trades])
        print(f"  平均单笔收益:      {avg_profit:+.2f}%")
        print(f"  最大单笔盈利:      {max_profit:+.2f}%")
        print(f"  最大单笔亏损:      {max_loss:+.2f}%")
        print(f"  平均持仓天数:      {avg_hold:.1f} 天")
    print()

    # 7. 理论最大收益
    theo_return, theo_trades = compute_max_theoretical_profit(df)
    print("─" * 65)
    print(f"理论最大波段收益（完美择时）: {theo_return:+.2f}%")
    print(f"  完美交易次数: {len(theo_trades)} 笔")
    print(f"  策略捕获率:   {strategy_return / theo_return * 100:.1f}%" if theo_return != 0 else "")
    print("=" * 65)

    # 8. 绘图
    if plot:
        plot_swing_trades(df, signals, trades, theo_trades)

    return signals, trades


# ─────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="趋势反转识别 & 波段收益最大化",
        epilog="示例: python swing_reversal.py 600519",
    )
    parser.add_argument("code", nargs="?", default=None,
                        help="股票代码（如 600519、000001），自动识别市场")
    parser.add_argument("--market", default=None, help="手动指定市场 (SZ/SH)，覆盖自动识别")
    parser.add_argument("--count", type=int, default=250, help="K线数量")
    parser.add_argument("--demo", action="store_true", help="使用模拟数据")
    parser.add_argument("--no-plot", action="store_true", help="不绘图")
    args = parser.parse_args()

    if args.demo:
        print("使用模拟数据（多段波段走势）...\n")
        df = generate_demo_data()
    elif args.code:
        market = args.market or guess_market(args.code)
        print(f"获取 {market} {args.code} 日K数据 ({args.count}根)...\n")
        df = fetch_real_data(market, args.code, args.count)
    else:
        parser.print_help()
        print("\n错误: 请提供股票代码，如: python swing_reversal.py 600519")
        raise SystemExit(1)

    analyze(df, plot=not args.no_plot)


if __name__ == "__main__":
    main()
