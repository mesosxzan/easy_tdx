"""综合因子分析脚本：提取买入日全量技术指标，对比盈亏交易。

对多只个股运行 shadow_yang_v2 策略回测，配对买入->卖出交易，
提取买入信号日的全量技术指标（含策略未使用的 RSI/MACD/KDJ/BOLL/CCI/WR/MFI），
按盈亏分组对比，挖掘可提升胜率与盈亏比的新过滤因子。

用法::

    python scripts/analyze_factor_discrimination.py
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx import MyTT  # noqa: E402
from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategy import Strategy  # noqa: E402
from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────────

STRATEGY_FILE = PROJECT_ROOT / "strategies" / "shadow_yang_v2.py"
KLINE_COUNT = 500
CASH = 100000.0
COMMISSION = 0.0003

# 股票池：5 组 wencai 随机查询结果 + 原始测试股，覆盖活跃/中盘/强势/低价/换手率风格
STOCK_POOL: list[tuple[str, str]] = [
    # 原始测试股（基准对照）
    ("600206", "SH"), ("002407", "SZ"), ("002156", "SZ"), ("600539", "SH"),
    ("000001", "SZ"), ("600519", "SH"), ("000858", "SZ"), ("600550", "SH"),
    ("600176", "SH"), ("600403", "SH"), ("300207", "SZ"),
    # wencai 随机批次1: 今日成交量大于20万手（活跃大盘股）
    ("000725", "SZ"), ("601991", "SH"), ("600664", "SH"), ("000100", "SZ"),
    ("601678", "SH"), ("002185", "SZ"), ("600157", "SH"), ("601288", "SH"),
    ("600744", "SH"), ("002498", "SZ"),
    # wencai 随机批次2: 总市值50-200亿 市盈率20-40（中盘价值股）
    ("001301", "SZ"), ("600004", "SH"), ("301035", "SZ"), ("300199", "SZ"),
    ("002518", "SZ"), ("300821", "SZ"), ("603833", "SH"), ("002461", "SZ"),
    ("002402", "SZ"), ("600339", "SH"),
    # wencai 随机批次3: 近60日涨幅超过30%（强势股）
    ("603823", "SH"), ("920725", "BJ"), ("920699", "BJ"), ("603580", "SH"),
    ("301319", "SZ"), ("301520", "SZ"), ("603137", "SH"),
    # wencai 随机批次4: 股价5-20元 主板（中低价主板股）
    ("002189", "SZ"), ("002853", "SZ"), ("600188", "SH"), ("603057", "SH"),
    ("002879", "SZ"), ("002270", "SZ"), ("600671", "SH"), ("002810", "SZ"),
    ("600729", "SH"), ("002265", "SZ"),
    # wencai 随机批次5: 周换手率超过20%（高换手率股）
    ("920088", "BJ"), ("300164", "SZ"),
]


# ── 特征数据类 ──────────────────────────────────────────────────────────────


def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > 1e-9 else 0.0


def _safe_float(arr: Any, idx: int) -> float:
    """安全取数组值，处理 NaN 和越界。"""
    try:
        val = float(arr[idx])
        return val if val == val else 0.0  # NaN check
    except (IndexError, TypeError, ValueError):
        return 0.0


@dataclass
class TradeFeatures:
    """买入日全量技术指标特征。"""

    stock: str
    buy_date: str
    sell_date: str
    buy_reason: str
    sell_reason: str
    buy_price: float
    sell_price: float
    profit_pct: float
    is_win: bool
    holding_bars: int

    # ── 策略已有指标 ──────────────────────────────────────────────────────
    adx: float
    pdi: float
    mdi: float
    pdi_mdi_ratio: float
    is_strong_trend: bool
    atr_pct: float  # ATR/价格
    vs_ma5: float
    vs_ma10: float
    vs_ma20: float
    vs_ma60: float
    is_bullish_align: bool
    ma5_slope: float
    recent_gain3: float
    recent_gain5: float
    body_pct: float
    body_ratio: float
    upper_shadow_ratio: float
    lower_shadow_ratio: float
    vol_ratio: float
    reversal_ratio: float
    prev5_drop: float

    # ── 策略未使用的新因子 ────────────────────────────────────────────────
    # RSI(14)
    rsi: float
    # MACD(12,26,9)
    macd_diff: float
    macd_dea: float
    macd_hist: float
    macd_hist_prev: float  # 前日 MACD 柱（判断柱状线趋势）
    macd_hist_slope: float  # MACD 柱斜率（今日-前日）
    # KDJ(9,3,3)
    kdj_k: float
    kdj_d: float
    kdj_j: float
    kdj_cross: bool  # K > D 金叉
    # BOLL(20,2)
    boll_upper: float
    boll_mid: float
    boll_lower: float
    boll_pctb: float  # %b 指标：(close-lower)/(upper-lower)
    boll_width: float  # 带宽：(upper-lower)/mid
    # CCI(14)
    cci: float
    # WR(14) Williams %R
    wr: float
    # MFI(14) 资金流量指标
    mfi: float
    # BIAS(6,12,24)
    bias6: float
    bias12: float
    bias24: float


def _extract_features(
    stock: str,
    df: pd.DataFrame,
    buy_row: pd.Series,
    sell_row: pd.Series,
    buy_reason: str,
    sell_reason: str,
) -> TradeFeatures:
    """提取买入日的全量技术指标特征。"""

    buy_dt = pd.Timestamp(buy_row["datetime"])
    dt_col = pd.to_datetime(df["datetime"])
    matches = dt_col[dt_col == buy_dt].index.tolist()
    if not matches:
        diffs = (dt_col - buy_dt).abs()
        matches = [int(diffs.idxmin())]
    idx = matches[0]

    o = float(df.iloc[idx]["open"])
    c = float(df.iloc[idx]["close"])
    h = float(df.iloc[idx]["high"])
    low_val = float(df.iloc[idx]["low"])
    v = float(df.iloc[idx]["vol"])

    prev_o = float(df.iloc[idx - 1]["open"]) if idx >= 1 else o
    prev_c = float(df.iloc[idx - 1]["close"]) if idx >= 1 else c

    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    vols = df["vol"].values.astype(float)

    # ── 策略已有指标 ──────────────────────────────────────────────────────
    ma5 = float(np.mean(closes[max(0, idx - 4) : idx + 1])) if idx >= 4 else c
    ma10 = float(np.mean(closes[max(0, idx - 9) : idx + 1])) if idx >= 9 else c
    ma20 = float(np.mean(closes[max(0, idx - 19) : idx + 1])) if idx >= 19 else c
    ma60 = float(np.mean(closes[max(0, idx - 59) : idx + 1])) if idx >= 59 else c
    ma5_prev = float(np.mean(closes[max(0, idx - 5) : idx])) if idx >= 5 else c

    vs_ma5 = _safe_div(c - ma5, ma5) * 100
    vs_ma10 = _safe_div(c - ma10, ma10) * 100
    vs_ma20 = _safe_div(c - ma20, ma20) * 100
    vs_ma60 = _safe_div(c - ma60, ma60) * 100
    is_bullish_align = ma5 > ma20 > ma60 and ma10 > ma20 > ma60
    ma5_slope = _safe_div(ma5 - ma5_prev, ma5_prev) * 100

    pdi_arr, mdi_arr, adx_arr, _ = MyTT.DMI(closes, highs, lows)
    adx = _safe_float(adx_arr, idx)
    pdi = _safe_float(pdi_arr, idx)
    mdi = _safe_float(mdi_arr, idx)
    pdi_mdi_ratio = _safe_div(pdi, mdi)
    is_strong_trend = adx >= 30.0 and pdi > mdi

    # ATR
    atr_period = 14
    if idx >= atr_period:
        tr_sum = 0.0
        for j in range(atr_period):
            offset = -j if j > 0 else 0
            tr1 = highs[idx + offset] - lows[idx + offset]
            tr2 = abs(highs[idx + offset] - closes[idx + offset - 1])
            tr3 = abs(lows[idx + offset] - closes[idx + offset - 1])
            tr_sum += max(tr1, tr2, tr3)
        atr = tr_sum / atr_period
    else:
        atr = c * 0.03
    atr_pct = _safe_div(atr, c) * 100

    close_3ago = float(closes[idx - 3]) if idx >= 3 else c
    close_5ago = float(closes[idx - 5]) if idx >= 5 else c
    recent_gain3 = _safe_div(c - close_3ago, close_3ago) * 100
    recent_gain5 = _safe_div(c - close_5ago, close_5ago) * 100
    prev5_drop = (
        _safe_div(float(closes[idx - 1]) - float(closes[idx - 6]), float(closes[idx - 6])) * 100
        if idx >= 6
        else 0.0
    )

    body_pct = _safe_div(c - o, o) * 100
    full_range = h - low_val
    body_ratio = _safe_div(abs(c - o), full_range)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - low_val
    body_val = abs(c - o)
    upper_shadow_ratio = _safe_div(upper_shadow, body_val)
    lower_shadow_ratio = _safe_div(lower_shadow, body_val)
    vol_ma5 = float(np.mean(vols[max(0, idx - 5) : idx])) if idx >= 5 else float(v)
    vol_ratio = _safe_div(float(v), vol_ma5)
    reversal_ratio = _safe_div(abs(c - o), abs(prev_o - prev_c)) if prev_o != prev_c else 0.0

    # ── 新因子：RSI(14) ──────────────────────────────────────────────────
    rsi_arr = MyTT.RSI(closes, 14)
    rsi = _safe_float(rsi_arr, idx)

    # ── 新因子：MACD(12,26,9) ────────────────────────────────────────────
    macd_diff_arr, macd_dea_arr, macd_hist_arr = MyTT.MACD(closes, 12, 26, 9)
    macd_diff = _safe_float(macd_diff_arr, idx)
    macd_dea = _safe_float(macd_dea_arr, idx)
    macd_hist = _safe_float(macd_hist_arr, idx)
    macd_hist_prev = _safe_float(macd_hist_arr, idx - 1) if idx >= 1 else 0.0
    macd_hist_slope = macd_hist - macd_hist_prev

    # ── 新因子：KDJ(9,3,3) ───────────────────────────────────────────────
    kdj_k_arr, kdj_d_arr, kdj_j_arr = MyTT.KDJ(closes, highs, lows, 9, 3, 3)
    kdj_k = _safe_float(kdj_k_arr, idx)
    kdj_d = _safe_float(kdj_d_arr, idx)
    kdj_j = _safe_float(kdj_j_arr, idx)
    kdj_cross = kdj_k > kdj_d  # 金叉

    # ── 新因子：BOLL(20,2) ───────────────────────────────────────────────
    boll_upper_arr, boll_mid_arr, boll_lower_arr = MyTT.BOLL(closes, 20, 2)
    boll_upper = _safe_float(boll_upper_arr, idx)
    boll_mid = _safe_float(boll_mid_arr, idx)
    boll_lower = _safe_float(boll_lower_arr, idx)
    boll_width = _safe_div(boll_upper - boll_lower, boll_mid) * 100
    boll_pctb = _safe_div(c - boll_lower, boll_upper - boll_lower)

    # ── 新因子：CCI(14) ──────────────────────────────────────────────────
    cci_arr = MyTT.CCI(closes, highs, lows, 14)
    cci = _safe_float(cci_arr, idx)

    # ── 新因子：WR(14) Williams %R ───────────────────────────────────────
    wr_arr, _ = MyTT.WR(closes, highs, lows, 14, 14)
    wr = _safe_float(wr_arr, idx)

    # ── 新因子：MFI(14) 资金流量指标 ─────────────────────────────────────
    mfi_arr = MyTT.MFI(closes, highs, lows, vols, 14)
    mfi = _safe_float(mfi_arr, idx)

    # ── 新因子：BIAS(6,12,24) ────────────────────────────────────────────
    bias6_arr, bias12_arr, bias24_arr = MyTT.BIAS(closes, 6, 12, 24)
    bias6 = _safe_float(bias6_arr, idx)
    bias12 = _safe_float(bias12_arr, idx)
    bias24 = _safe_float(bias24_arr, idx)

    # ── 交易结果 ──────────────────────────────────────────────────────────
    buy_price = float(buy_row["price"])
    sell_price = float(sell_row["price"])
    cost_basis = float(sell_row.get("cost_basis", 0))
    pnl = float(sell_row.get("pnl", 0))
    if cost_basis > 0:
        profit_pct = _safe_div(pnl, cost_basis) * 100
    else:
        profit_pct = _safe_div(sell_price - buy_price, buy_price) * 100

    holding_bars = int(
        (pd.Timestamp(sell_row["datetime"]) - pd.Timestamp(buy_row["datetime"])).days
    )

    buy_date_str = str(buy_row["datetime"]).split("T")[0]
    sell_date_str = str(sell_row["datetime"]).split("T")[0]

    return TradeFeatures(
        stock=stock,
        buy_date=buy_date_str,
        sell_date=sell_date_str,
        buy_reason=buy_reason,
        sell_reason=sell_reason,
        buy_price=buy_price,
        sell_price=sell_price,
        profit_pct=profit_pct,
        is_win=profit_pct > 0,
        holding_bars=holding_bars,
        adx=adx,
        pdi=pdi,
        mdi=mdi,
        pdi_mdi_ratio=pdi_mdi_ratio,
        is_strong_trend=is_strong_trend,
        atr_pct=atr_pct,
        vs_ma5=vs_ma5,
        vs_ma10=vs_ma10,
        vs_ma20=vs_ma20,
        vs_ma60=vs_ma60,
        is_bullish_align=is_bullish_align,
        ma5_slope=ma5_slope,
        recent_gain3=recent_gain3,
        recent_gain5=recent_gain5,
        body_pct=body_pct,
        body_ratio=body_ratio,
        upper_shadow_ratio=upper_shadow_ratio,
        lower_shadow_ratio=lower_shadow_ratio,
        vol_ratio=vol_ratio,
        reversal_ratio=reversal_ratio,
        prev5_drop=prev5_drop,
        rsi=rsi,
        macd_diff=macd_diff,
        macd_dea=macd_dea,
        macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev,
        macd_hist_slope=macd_hist_slope,
        kdj_k=kdj_k,
        kdj_d=kdj_d,
        kdj_j=kdj_j,
        kdj_cross=kdj_cross,
        boll_upper=boll_upper,
        boll_mid=boll_mid,
        boll_lower=boll_lower,
        boll_pctb=boll_pctb,
        boll_width=boll_width,
        cci=cci,
        wr=wr,
        mfi=mfi,
        bias6=bias6,
        bias12=bias12,
        bias24=bias24,
    )


# ── 回测执行 ────────────────────────────────────────────────────────────────


def _load_strategy(strategy_path: Path) -> type[Strategy]:
    spec = importlib.util.spec_from_file_location("strategy_mod", strategy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载策略文件: {strategy_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ShadowYangStrategy  # type: ignore[attr-defined]


def _run_backtest(
    stock: str, market: str, strategy_cls: type[Strategy], client: MacClient
) -> list[TradeFeatures]:
    try:
        df = client.get_stock_kline(
            parse_market(market),
            stock,
            period=parse_period("DAILY"),
            start=0,
            count=KLINE_COUNT,
            adjust=parse_adjust("QFQ"),
        )
    except Exception as e:
        print(f"  [SKIP] {stock}: 行情获取失败 {e}")
        return []

    if df is None or len(df) < 60:
        print(f"  [SKIP] {stock}: K线数据不足 ({len(df) if df is not None else 0} 根)")
        return []

    try:
        engine = BacktestEngine(strategy=strategy_cls, cash=CASH, commission=COMMISSION)
        result = engine.run(df)
    except Exception as e:
        print(f"  [SKIP] {stock}: 回测失败 {e}")
        return []

    trades = result.trades
    if trades is None or len(trades) == 0:
        print(f"  [SKIP] {stock}: 无交易")
        return []

    features_list: list[TradeFeatures] = []
    buy_rows = trades[trades["direction"] == "BUY"].copy()
    sell_rows = trades[trades["direction"] == "SELL"].copy()

    for _, buy_row in buy_rows.iterrows():
        buy_dt = pd.Timestamp(buy_row["datetime"])
        matching_sells = sell_rows[sell_rows["datetime"] > buy_dt]
        if matching_sells.empty:
            continue
        sell_row = matching_sells.iloc[0]

        buy_reason = str(buy_row.get("reason", ""))
        sell_reason = str(sell_row.get("reason", ""))

        try:
            features = _extract_features(stock, df, buy_row, sell_row, buy_reason, sell_reason)
            features_list.append(features)
        except Exception as e:
            print(f"  [WARN] {stock}: 特征提取失败 {e}")

    print(f"  {stock}: {len(features_list)} 笔交易")
    return features_list


# ── 统计分析 ────────────────────────────────────────────────────────────────


def _analyze_features(all_features: list[TradeFeatures]) -> None:
    """按盈亏分组对比各因子，输出区分度排名。"""

    if not all_features:
        print("无交易数据可分析")
        return

    rows = []
    for f in all_features:
        row = {}
        for field in fields(f):
            row[field.name] = getattr(f, field.name)
        rows.append(row)

    df = pd.DataFrame(rows)
    winners = df[df["is_win"] == True]  # noqa: E712
    losers = df[df["is_win"] == False]  # noqa: E712

    print(f"\n{'='*80}")
    print(f"总交易数: {len(df)} | 盈利: {len(winners)} ({len(winners)/len(df)*100:.1f}%) | "
          f"亏损: {len(losers)} ({len(losers)/len(df)*100:.1f}%)")
    print(f"平均盈利: {winners['profit_pct'].mean():.2f}% | "
          f"平均亏损: {losers['profit_pct'].mean():.2f}%")
    if len(losers) > 0:
        pf = winners['profit_pct'].sum() / abs(losers['profit_pct'].sum())
    else:
        pf = float('inf')
    print(f"盈亏比(PF): {pf:.2f}")
    print(f"{'='*80}\n")

    # 数值型因子对比
    numeric_cols = [
        # 策略已有指标
        "adx", "pdi", "mdi", "pdi_mdi_ratio", "atr_pct",
        "vs_ma5", "vs_ma10", "vs_ma20", "vs_ma60",
        "ma5_slope", "recent_gain3", "recent_gain5",
        "body_pct", "body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
        "vol_ratio", "reversal_ratio", "prev5_drop",
        # 新因子
        "rsi", "macd_diff", "macd_dea", "macd_hist", "macd_hist_prev", "macd_hist_slope",
        "kdj_k", "kdj_d", "kdj_j",
        "boll_pctb", "boll_width",
        "cci", "wr", "mfi",
        "bias6", "bias12", "bias24",
    ]

    print(f"{'因子':<20} {'盈利均值':>10} {'亏损均值':>10} {'差异':>10} {'区分度':>8} {'方向':>6}")
    print("-" * 75)

    results = []
    for col in numeric_cols:
        win_mean = winners[col].mean()
        lose_mean = losers[col].mean()
        diff = win_mean - lose_mean
        # 区分度：标准化差异（除以整体标准差）
        std = df[col].std()
        discriminant = abs(diff) / std if std > 1e-9 else 0.0
        direction = "↑" if diff > 0 else "↓"
        results.append((col, win_mean, lose_mean, diff, discriminant, direction))

    # 按区分度排序
    results.sort(key=lambda x: x[4], reverse=True)
    for col, win_mean, lose_mean, diff, disc, direction in results:
        print(f"{col:<20} {win_mean:>10.3f} {lose_mean:>10.3f} "
              f"{diff:>+10.3f} {disc:>8.3f} {direction:>6}")

    # 布尔型因子胜率对比
    print(f"\n{'='*80}")
    print("布尔型因子胜率对比")
    print(f"{'='*80}")
    bool_cols = ["is_strong_trend", "is_bullish_align", "kdj_cross"]
    for col in bool_cols:
        for val in [True, False]:
            subset = df[df[col] == val]
            if len(subset) > 0:
                win_rate = subset["is_win"].mean() * 100
                avg_profit = subset["profit_pct"].mean()
                print(f"  {col}={val}: {len(subset)}笔, "
                      f"胜率{win_rate:.1f}%, 均收益{avg_profit:+.2f}%")

    # 关键因子分桶胜率分析
    print(f"\n{'='*80}")
    print("关键因子分桶胜率分析")
    print(f"{'='*80}")

    _bucket_analysis(df, "rsi", [0, 30, 40, 50, 60, 70, 80, 100], "RSI")
    _bucket_analysis(df, "macd_hist", [-999, -0.5, -0.1, 0, 0.1, 0.5, 999], "MACD柱")
    _bucket_analysis(df, "kdj_j", [-999, 0, 20, 50, 80, 100, 999], "KDJ-J")
    _bucket_analysis(df, "boll_pctb", [-999, 0, 0.2, 0.5, 0.8, 1.0, 1.2, 999], "BOLL %b")
    _bucket_analysis(df, "cci", [-999, -100, 0, 100, 200, 999], "CCI")
    _bucket_analysis(df, "wr", [-999, -80, -50, -20, 0, 999], "WR")
    _bucket_analysis(df, "mfi", [0, 20, 40, 60, 80, 100], "MFI")
    _bucket_analysis(df, "adx", [0, 15, 20, 30, 40, 50, 999], "ADX")
    _bucket_analysis(df, "vs_ma20", [-999, -5, 0, 5, 10, 15, 25, 999], "vsMA20")

    # 保存完整数据到 CSV
    output_path = PROJECT_ROOT / "scripts" / "factor_analysis_results.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n完整数据已保存至: {output_path}")


def _bucket_analysis(df: pd.DataFrame, col: str, bins: list[float], label: str) -> None:
    """分桶胜率分析。"""
    print(f"\n  {label} 分桶胜率:")
    print(f"  {'区间':<20} {'笔数':>6} {'胜率':>8} {'均收益':>10}")
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        if i == 0:
            subset = df[df[col] <= hi]
        elif i == len(bins) - 2:
            subset = df[df[col] > lo]
        else:
            subset = df[(df[col] > lo) & (df[col] <= hi)]
        if len(subset) == 0:
            continue
        win_rate = subset["is_win"].mean() * 100
        avg_profit = subset["profit_pct"].mean()
        range_str = f"({lo:.1f}, {hi:.1f}]" if i > 0 else f"<= {hi:.1f}"
        if i == len(bins) - 2:
            range_str = f"> {lo:.1f}"
        print(f"  {range_str:<20} {len(subset):>6} {win_rate:>7.1f}% {avg_profit:>+9.2f}%")


# ── 主函数 ──────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 80)
    print("综合因子分析：提取买入日全量技术指标，对比盈亏交易")
    print(f"策略: {STRATEGY_FILE.name} | 股票池: {len(STOCK_POOL)} 只 | K线: {KLINE_COUNT} 根")
    print("=" * 80)

    strategy_cls = _load_strategy(STRATEGY_FILE)
    all_features: list[TradeFeatures] = []

    client = MacClient.from_best_host()
    for stock, market in STOCK_POOL:
        print(f"\n[{stock}] 处理中...")
        features = _run_backtest(stock, market, strategy_cls, client)
        all_features.extend(features)
    client.close()

    print(f"\n{'='*80}")
    print(f"采集完成: {len(all_features)} 笔交易")
    _analyze_features(all_features)


if __name__ == "__main__":
    main()
