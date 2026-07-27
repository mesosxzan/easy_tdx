"""shadow_yang.py 原始策略亏损单买入特征诊断脚本。

对 600326 及多只标的运行 shadow_yang.py 回测，配对买入->卖出交易，
提取买入信号日的各指标特征，按盈亏分组对比，挖掘区分胜败交易的关键特征。

用法::

    python scripts/diag_shadow_yang_buy_features.py
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
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

STRATEGY_FILE = PROJECT_ROOT / "strategies" / "shadow_yang.py"
KLINE_COUNT = 1000
CASH = 100000.0
COMMISSION = 0.0003

STOCK_POOL: list[tuple[str, str, str]] = [
    ("600326", "SH", "目标股"),
    ("600206", "SZ", "震荡上行"),
    ("002407", "SZ", "大波动"),
    ("002156", "SZ", "趋势"),
    ("600539", "SH", "大牛股"),
    ("000001", "SZ", "银行阴跌"),
    ("600519", "SH", "白酒慢牛"),
    ("000858", "SZ", "白酒"),
    ("600550", "SH", "波段"),
    ("600176", "SH", "多头"),
]


# ── 特征提取 ────────────────────────────────────────────────────────────────


def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > 1e-9 else 0.0


@dataclass
class TradeFeatures:
    stock: str
    buy_date: str
    sell_date: str
    buy_price: float
    sell_price: float
    profit_pct: float
    is_win: bool
    holding_bars: int
    # K线实体
    body_pct: float
    body_ratio: float
    # 影线
    upper_shadow_ratio: float
    lower_shadow_ratio: float
    # 前日阴线
    prev_body_pct: float
    reversal_ratio: float
    # 成交量
    vol_ratio: float
    vol_change_pct: float
    # 均线位置
    vs_ma5: float
    vs_ma10: float
    vs_ma20: float
    vs_ma60: float
    # 均线排列
    is_bullish_align: bool
    ma5_above_ma10: bool
    ma5_slope: float
    # 趋势
    adx: float
    pdi: float
    mdi: float
    pdi_mdi_ratio: float
    is_strong_trend: bool
    # 涨幅
    recent_gain3: float
    recent_gain5: float
    # 前期趋势（买入前5日跌幅，判断是否下跌中继抄底）
    prev5_drop: float
    # 买入前3日是否连续阴线
    prev3_yin_count: int
    # 形态特征
    is_piercing: bool
    is_engulfing: bool
    is_morning_star: bool
    is_strong_reversal: bool
    is_ma5_up: bool
    has_lower_shadow: bool


def _extract_features(
    stock: str,
    df: pd.DataFrame,
    buy_row: pd.Series,
    sell_row: pd.Series,
) -> TradeFeatures:
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

    prev_o = float(df.iloc[idx - 1]["open"])
    prev_c = float(df.iloc[idx - 1]["close"])
    prev_h = float(df.iloc[idx - 1]["high"])
    prev_l = float(df.iloc[idx - 1]["low"])
    prev_v = float(df.iloc[idx - 1]["vol"])

    prev2_o = float(df.iloc[idx - 2]["open"]) if idx >= 2 else prev_o
    prev2_c = float(df.iloc[idx - 2]["close"]) if idx >= 2 else prev_c

    body_abs = c - o
    body_pct = _safe_div(c - o, o) * 100
    full_range = h - low_val
    body_ratio = _safe_div(abs(c - o), full_range)

    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - low_val
    body_val = abs(c - o)
    upper_shadow_ratio = _safe_div(upper_shadow, body_val)
    lower_shadow_ratio = _safe_div(lower_shadow, body_val)

    prev_body_pct = _safe_div(prev_o - prev_c, prev_o) * 100
    reversal_ratio = _safe_div(abs(c - o), abs(prev_o - prev_c)) if prev_o != prev_c else 0.0

    vol_ma5 = float(df.iloc[idx - 5 : idx]["vol"].mean()) if idx >= 5 else v
    vol_ratio = _safe_div(v, vol_ma5)
    vol_change_pct = _safe_div(v - prev_v, prev_v) * 100

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    ma5 = float(np.mean(closes[idx - 4 : idx + 1])) if idx >= 4 else c
    ma10 = float(np.mean(closes[idx - 9 : idx + 1])) if idx >= 9 else c
    ma20 = float(np.mean(closes[idx - 19 : idx + 1])) if idx >= 19 else c
    ma60 = float(np.mean(closes[idx - 59 : idx + 1])) if idx >= 59 else c
    ma5_prev = float(np.mean(closes[idx - 5 : idx])) if idx >= 5 else c

    vs_ma5 = _safe_div(c - ma5, ma5) * 100
    vs_ma10 = _safe_div(c - ma10, ma10) * 100
    vs_ma20 = _safe_div(c - ma20, ma20) * 100
    vs_ma60 = _safe_div(c - ma60, ma60) * 100

    is_bullish_align = ma5 > ma20 > ma60 and ma10 > ma20 > ma60
    ma5_above_ma10 = ma5 > ma10
    ma5_slope = _safe_div(ma5 - ma5_prev, ma5_prev) * 100
    is_ma5_up = ma5 > ma5_prev

    pdi_arr, mdi_arr, adx_arr, _ = MyTT.DMI(closes, highs, lows)
    adx = float(adx_arr[idx]) if idx < len(adx_arr) else 0.0
    pdi = float(pdi_arr[idx]) if idx < len(pdi_arr) else 0.0
    mdi = float(mdi_arr[idx]) if idx < len(mdi_arr) else 0.0
    pdi_mdi_ratio = _safe_div(pdi, mdi)
    is_strong_trend = adx >= 30.0 and pdi > mdi

    close_3ago = float(closes[idx - 3]) if idx >= 3 else c
    close_5ago = float(closes[idx - 5]) if idx >= 5 else c
    recent_gain3 = _safe_div(c - close_3ago, close_3ago) * 100
    recent_gain5 = _safe_div(c - close_5ago, close_5ago) * 100

    # 买入前5日跌幅（从5日前到1日前的跌幅）
    prev5_drop = _safe_div(float(closes[idx - 1]) - float(closes[idx - 6]), float(closes[idx - 6])) * 100 if idx >= 6 else 0.0
    # 买入前3日阴线数量
    prev3_yin_count = sum(1 for k in range(1, 4) if idx - k >= 0 and float(closes[idx - k]) < float(df.iloc[idx - k]["open"]))

    is_piercing = _check_piercing(o, c, prev_o, prev_c, prev_l)
    is_engulfing = _check_engulfing(o, c, prev_o, prev_c)
    is_morning_star = _check_morning_star(o, c, prev_o, prev_c, prev2_o, prev2_c)
    is_strong_reversal = _check_strong_reversal(o, c, prev_o, prev_c)
    has_lower_shadow = lower_shadow > body_val * 0.3

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
        buy_price=buy_price,
        sell_price=sell_price,
        profit_pct=profit_pct,
        is_win=profit_pct > 0,
        holding_bars=holding_bars,
        body_pct=body_pct,
        body_ratio=body_ratio,
        upper_shadow_ratio=upper_shadow_ratio,
        lower_shadow_ratio=lower_shadow_ratio,
        prev_body_pct=prev_body_pct,
        reversal_ratio=reversal_ratio,
        vol_ratio=vol_ratio,
        vol_change_pct=vol_change_pct,
        vs_ma5=vs_ma5,
        vs_ma10=vs_ma10,
        vs_ma20=vs_ma20,
        vs_ma60=vs_ma60,
        is_bullish_align=is_bullish_align,
        ma5_above_ma10=ma5_above_ma10,
        ma5_slope=ma5_slope,
        adx=adx,
        pdi=pdi,
        mdi=mdi,
        pdi_mdi_ratio=pdi_mdi_ratio,
        is_strong_trend=is_strong_trend,
        recent_gain3=recent_gain3,
        recent_gain5=recent_gain5,
        prev5_drop=prev5_drop,
        prev3_yin_count=prev3_yin_count,
        is_piercing=is_piercing,
        is_engulfing=is_engulfing,
        is_morning_star=is_morning_star,
        is_strong_reversal=is_strong_reversal,
        is_ma5_up=is_ma5_up,
        has_lower_shadow=has_lower_shadow,
    )


def _check_piercing(cur_o, cur_c, prev_o, prev_c, prev_l):
    if prev_c >= prev_o:
        return False
    if cur_o >= prev_l:
        return False
    midpoint = (prev_o + prev_c) / 2
    return cur_c > midpoint and cur_c < prev_o


def _check_engulfing(cur_o, cur_c, prev_o, prev_c):
    if prev_c >= prev_o:
        return False
    if cur_c <= cur_o:
        return False
    return cur_o <= prev_c and cur_c >= prev_o


def _check_morning_star(cur_o, cur_c, prev_o, prev_c, prev2_o, prev2_c):
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


def _check_strong_reversal(cur_o, cur_c, prev_o, prev_c):
    if prev_c >= prev_o:
        return False
    if cur_c <= cur_o:
        return False
    if cur_c < prev_o:
        return False
    prev_body = prev_o - prev_c
    cur_body = cur_c - cur_o
    return cur_body >= prev_body * 2.0


def _load_strategy_class(file_path: Path) -> type[Strategy]:
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


def _run_backtest(client, code, market_str, strategy_cls):
    mkt = parse_market(market_str)
    df = client.get_stock_kline(
        mkt, code, period=parse_period("DAILY"), start=0,
        count=KLINE_COUNT, adjust=parse_adjust("QFQ"),
    )
    engine = BacktestEngine(strategy=strategy_cls, cash=CASH, commission=COMMISSION)
    result = engine.run(df)
    return df, result


def _extract_trades(df, result, code):
    trades = result.trades
    if trades.empty:
        return []
    features_list = []
    buy_rows = trades[trades["direction"] == "BUY"].copy()
    for _, buy_row in buy_rows.iterrows():
        if buy_row.get("rejected", False):
            continue
        buy_dt = pd.Timestamp(buy_row["datetime"])
        sell_rows = trades[
            (trades["direction"] == "SELL")
            & (pd.to_datetime(trades["datetime"]) >= buy_dt)
            & (~trades.get("rejected", False))
        ]
        if sell_rows.empty:
            continue
        sell_row = sell_rows.iloc[0]
        try:
            features = _extract_features(code, df, buy_row, sell_row)
            features_list.append(features)
        except (IndexError, KeyError, ValueError) as e:
            print(f"  [!] {code} 买入 {buy_dt} 特征提取失败: {e}")
    return features_list


def _print_stock_summary(code, stock_type, result):
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


def main():
    print("=" * 100)
    print("shadow_yang.py 原始策略 - 亏损单买入特征诊断")
    print(f"策略: {STRATEGY_FILE.name} | K线: {KLINE_COUNT} | 股票: {len(STOCK_POOL)} 只")
    print("=" * 100)

    strategy_cls = _load_strategy_class(STRATEGY_FILE)
    print(f"策略类: {strategy_cls.__name__}")

    print("\n正在连接行情服务器...")
    client = MacClient.from_best_host()
    client.connect()

    all_features: list[TradeFeatures] = []
    stock_features: dict[str, list[TradeFeatures]] = {}

    try:
        print(f"\n{'=' * 100}")
        print("回测结果摘要")
        print(f"{'=' * 100}")
        for code, market_str, stock_type in STOCK_POOL:
            try:
                df, result = _run_backtest(client, code, market_str, strategy_cls)
                _print_stock_summary(code, stock_type, result)
                features = _extract_trades(df, result, code)
                all_features.extend(features)
                stock_features[code] = features
            except Exception as e:
                print(f"  {code} ({stock_type}): 错误 - {e}")
    finally:
        client.close()

    if not all_features:
        print("\n[!] 未提取到任何交易特征。")
        return

    df_all = pd.DataFrame([f.__dict__ for f in all_features])
    print(f"\n共提取 {len(df_all)} 笔买入交易特征")

    # ── 600326 专项分析 ──
    print(f"\n{'#' * 100}")
    print("# 600326 专项分析")
    print(f"{'#' * 100}")
    df_600326 = df_all[df_all["stock"] == "600326"].copy()
    if len(df_600326) > 0:
        _analyze_stock(df_600326, "600326")

    # ── 全部股票汇总 ──
    print(f"\n{'#' * 100}")
    print("# 全部股票汇总分析")
    print(f"{'#' * 100}")
    _analyze_stock(df_all, "全部")

    # ── 逐股亏损单特征 ──
    print(f"\n{'#' * 100}")
    print("# 逐股亏损单买入特征明细")
    print(f"{'#' * 100}")
    for code, feats in stock_features.items():
        if not feats:
            continue
        df_s = pd.DataFrame([f.__dict__ for f in feats])
        losses = df_s[~df_s["is_win"]]
        wins = df_s[df_s["is_win"]]
        print(f"\n--- {code} ({len(df_s)}笔, 胜率{df_s['is_win'].mean():.1%}) ---")
        print(f"  盈利{len(wins)}笔均赚{wins['profit_pct'].mean():.2f}%, 亏损{len(losses)}笔均亏{losses['profit_pct'].mean():.2f}%")
        if len(losses) > 0:
            print(f"  亏损单特征均值: ADX={losses['adx'].mean():.1f} 量比={losses['vol_ratio'].mean():.2f} "
                  f"vsMA20={losses['vs_ma20'].mean():.2f}% 近3日涨幅={losses['recent_gain3'].mean():.2f}% "
                  f"前5日跌幅={losses['prev5_drop'].mean():.2f}% 多头排列率={losses['is_bullish_align'].mean():.1%}")
        if len(wins) > 0:
            print(f"  盈利单特征均值: ADX={wins['adx'].mean():.1f} 量比={wins['vol_ratio'].mean():.2f} "
                  f"vsMA20={wins['vs_ma20'].mean():.2f}% 近3日涨幅={wins['recent_gain3'].mean():.2f}% "
                  f"前5日跌幅={wins['prev5_drop'].mean():.2f}% 多头排列率={wins['is_bullish_align'].mean():.1%}")


def _analyze_stock(df: pd.DataFrame, label: str):
    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]
    total = len(df)
    overall_wr = len(wins) / total if total > 0 else 0

    print(f"\n{'=' * 100}")
    print(f"{label}: {total} 笔交易, 胜率 {overall_wr:.1%}, "
          f"均盈亏 {df['profit_pct'].mean():.2f}%, "
          f"盈利均赚 {wins['profit_pct'].mean():.2f}%, "
          f"亏损均亏 {losses['profit_pct'].mean():.2f}%")
    print(f"{'=' * 100}")

    # 数值特征对比
    numeric_features = [
        ("body_pct", "阳线实体涨幅%", "越高越好"),
        ("body_ratio", "实体占振幅比", "越高越好"),
        ("upper_shadow_ratio", "上影线/实体比", "越低越好"),
        ("lower_shadow_ratio", "下影线/实体比", "越高越好"),
        ("reversal_ratio", "反转力度", "越高越好"),
        ("prev_body_pct", "前日阴线跌幅%", "适中最好"),
        ("vol_ratio", "量比(vs5日均量)", "越高越好"),
        ("vol_change_pct", "量变化%", "越高越好"),
        ("vs_ma5", "偏离MA5%", ""),
        ("vs_ma10", "偏离MA10%", ""),
        ("vs_ma20", "偏离MA20%", "过高=追高"),
        ("vs_ma60", "偏离MA60%", ""),
        ("ma5_slope", "MA5斜率%", "越高越好"),
        ("adx", "ADX趋势强度", "越高越好"),
        ("pdi_mdi_ratio", "+DI/-DI比", "越高越好"),
        ("recent_gain3", "近3日涨幅%", "越低越好(未追高)"),
        ("recent_gain5", "近5日涨幅%", "越低越好(未追高)"),
        ("prev5_drop", "前5日跌幅%", "下跌中继风险"),
        ("prev3_yin_count", "前3日阴线数", ""),
        ("holding_bars", "持仓天数", ""),
        ("profit_pct", "盈亏%", ""),
    ]

    print(f"\n{'特征':<22} {'说明':<22} {'盈利均值':>10} {'亏损均值':>10} "
          f"{'差异':>10} {'方向':<12}")
    print("-" * 100)
    for col, desc, hint in numeric_features:
        if col not in df.columns:
            continue
        win_mean = wins[col].mean() if len(wins) > 0 else 0.0
        loss_mean = losses[col].mean() if len(losses) > 0 else 0.0
        diff = win_mean - loss_mean
        if abs(diff) < 0.01:
            direction = "无明显差异"
        elif diff > 0:
            direction = "盈利>亏损"
        else:
            direction = "亏损>盈利"
        print(f"{col:<22} {hint:<22} {win_mean:>10.2f} {loss_mean:>10.2f} "
              f"{diff:>+10.2f} {direction:<12}")

    # 布尔特征胜率
    print(f"\n{'=' * 100}")
    print("布尔特征胜率分析")
    print(f"{'=' * 100}")
    bool_features = [
        ("is_bullish_align", "多头排列"),
        ("ma5_above_ma10", "MA5>MA10"),
        ("is_ma5_up", "MA5上行"),
        ("is_strong_trend", "强趋势(ADX>=30)"),
        ("is_piercing", "刺穿形态"),
        ("is_engulfing", "看涨吞没"),
        ("is_morning_star", "晨星形态"),
        ("is_strong_reversal", "强反转阳线"),
        ("has_lower_shadow", "有下影线"),
    ]
    print(f"\n{'特征':<22} {'说明':<16} {'=True胜率':>12} {'=False胜率':>12} "
          f"{'差异':>10} {'建议':<12}")
    print("-" * 90)
    for col, desc in bool_features:
        if col not in df.columns:
            continue
        true_trades = df[df[col]]
        false_trades = df[~df[col]]
        true_wr = true_trades["is_win"].mean() if len(true_trades) > 0 else 0.0
        false_wr = false_trades["is_win"].mean() if len(false_trades) > 0 else 0.0
        diff = true_wr - false_wr
        if abs(diff) < 0.05:
            advice = "无明显影响"
        elif diff > 0:
            advice = "倾向保留"
        else:
            advice = "倾向过滤"
        print(f"{col:<22} {desc:<16} {true_wr:>11.1%} {false_wr:>12.1%} "
              f"{diff:>+10.1%} {advice:<12}")

    # 区间分布分析
    print(f"\n{'=' * 100}")
    print("关键特征区间胜率分析")
    print(f"{'=' * 100}")
    features_to_bin = [
        ("body_pct", "阳线实体涨幅%", [-100, 1, 2, 3, 5, 8, 100]),
        ("reversal_ratio", "反转力度", [-100, 0.5, 1.0, 1.5, 2.0, 3.0, 100]),
        ("vol_ratio", "量比", [0, 0.8, 1.0, 1.2, 1.5, 2.0, 100]),
        ("adx", "ADX", [0, 15, 20, 25, 30, 40, 100]),
        ("recent_gain3", "近3日涨幅%", [-100, 0, 3, 6, 9, 12, 100]),
        ("recent_gain5", "近5日涨幅%", [-100, 0, 5, 10, 15, 20, 100]),
        ("vs_ma20", "偏离MA20%", [-100, 0, 3, 5, 10, 15, 100]),
        ("vs_ma60", "偏离MA60%", [-100, -5, 0, 5, 10, 15, 100]),
        ("prev5_drop", "前5日跌幅%", [-100, -5, 0, 3, 5, 10, 100]),
        ("pdi_mdi_ratio", "+DI/-DI比", [0, 0.5, 0.8, 1.0, 1.5, 2.0, 100]),
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
            wr = subset["is_win"].mean()
            avg_p = subset["profit_pct"].mean()
            wins_p = subset[subset["is_win"]]["profit_pct"].sum()
            losses_p = abs(subset[~subset["is_win"]]["profit_pct"].sum())
            pf = wins_p / losses_p if losses_p > 0 else float("inf")
            label_str = f"[{lo:.1f}, {hi:.1f})"
            print(f"{label_str:<20} {len(subset):>6} {wr:>7.1%} {avg_p:>10.2f} {pf:>10.2f}")

    # 600326 亏损单明细
    if label == "600326":
        print(f"\n{'=' * 100}")
        print("600326 亏损单明细（按盈亏排序）")
        print(f"{'=' * 100}")
        losses_sorted = losses.sort_values("profit_pct")
        print(f"{'买入日':>12} {'卖出日':>12} {'买价':>7} {'卖价':>7} {'盈亏%':>7} "
              f"{'实体%':>6} {'反转力':>6} {'量比':>5} {'ADX':>5} {'vsMA20':>7} "
              f"{'近3日%':>7} {'前5日跌':>7} {'多头':>4} {'强趋势':>6}")
        print("-" * 110)
        for _, row in losses_sorted.iterrows():
            print(f"{row['buy_date']:>12} {row['sell_date']:>12} {row['buy_price']:>7.2f} "
                  f"{row['sell_price']:>7.2f} {row['profit_pct']:>7.2f} "
                  f"{row['body_pct']:>6.2f} {row['reversal_ratio']:>6.2f} "
                  f"{row['vol_ratio']:>5.2f} {row['adx']:>5.1f} {row['vs_ma20']:>7.2f} "
                  f"{row['recent_gain3']:>7.2f} {row['prev5_drop']:>7.2f} "
                  f"{'是' if row['is_bullish_align'] else '否':>4} "
                  f"{'是' if row['is_strong_trend'] else '否':>6}")

        print(f"\n600326 盈利单明细（按盈亏排序）")
        print(f"{'=' * 100}")
        wins_sorted = wins.sort_values("profit_pct", ascending=False)
        print(f"{'买入日':>12} {'卖出日':>12} {'买价':>7} {'卖价':>7} {'盈亏%':>7} "
              f"{'实体%':>6} {'反转力':>6} {'量比':>5} {'ADX':>5} {'vsMA20':>7} "
              f"{'近3日%':>7} {'前5日跌':>7} {'多头':>4} {'强趋势':>6}")
        print("-" * 110)
        for _, row in wins_sorted.iterrows():
            print(f"{row['buy_date']:>12} {row['sell_date']:>12} {row['buy_price']:>7.2f} "
                  f"{row['sell_price']:>7.2f} {row['profit_pct']:>7.2f} "
                  f"{row['body_pct']:>6.2f} {row['reversal_ratio']:>6.2f} "
                  f"{row['vol_ratio']:>5.2f} {row['adx']:>5.1f} {row['vs_ma20']:>7.2f} "
                  f"{row['recent_gain3']:>7.2f} {row['prev5_drop']:>7.2f} "
                  f"{'是' if row['is_bullish_align'] else '否':>4} "
                  f"{'是' if row['is_strong_trend'] else '否':>6}")


if __name__ == "__main__":
    main()
