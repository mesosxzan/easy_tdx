"""MACD 指标计算（纯 numpy 实现）。

MACD 由三部分组成：
- DIF（快线）: EMA(fast) - EMA(slow)
- DEA（慢线）: EMA(DIF, signal)
- HIST（柱状图）: 2 * (DIF - DEA)
"""

from __future__ import annotations


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float]]:
    """计算 MACD 指标。

    Args:
        closes: 收盘价序列
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期

    Returns:
        {"dif": [...], "dea": [...], "hist": [...]}
    """
    n = len(closes)
    if n == 0:
        return {"dif": [], "dea": [], "hist": []}

    # EMA 计算
    ema_fast = _calc_ema(closes, fast)
    ema_slow = _calc_ema(closes, slow)

    # DIF = EMA(fast) - EMA(slow)
    dif = [ema_fast[i] - ema_slow[i] for i in range(n)]

    # DEA = EMA(DIF, signal)
    dea = _calc_ema(dif, signal)

    # HIST = 2 * (DIF - DEA)
    hist = [2.0 * (dif[i] - dea[i]) for i in range(n)]

    return {"dif": dif, "dea": dea, "hist": hist}


def _calc_ema(data: list[float], period: int) -> list[float]:
    """计算指数移动平均线。

    EMA(t) = price(t) * k + EMA(t-1) * (1 - k)
    k = 2 / (period + 1)
    """
    n = len(data)
    if n == 0:
        return []

    k = 2.0 / (period + 1)
    result = [0.0] * n

    # 初始值：第一个数据点
    result[0] = data[0]

    for i in range(1, n):
        result[i] = data[i] * k + result[i - 1] * (1 - k)

    return result


def calc_macd_force(
    closes: list[float],
    start_idx: int,
    end_idx: int,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float]:
    """计算区间内的 MACD 力度（用于背驰判断）。

    Args:
        closes: 完整收盘价序列
        start_idx: 起始索引
        end_idx: 结束索引（含）
        fast, slow, signal: MACD 参数

    Returns:
        {\"hist_sum\": 总柱子面积, \"hist_up_sum\": 红柱总和, \"hist_down_sum\": 绿柱总和}
    """
    if start_idx > end_idx or end_idx >= len(closes):
        return {"hist_sum": 0.0, "hist_up_sum": 0.0, "hist_down_sum": 0.0}

    macd = calc_macd(closes, fast, slow, signal)
    hist_slice = macd["hist"][start_idx : end_idx + 1]

    hist_abs = [abs(h) for h in hist_slice]
    hist_up = [h for h in hist_slice if h > 0]
    hist_down = [h for h in hist_slice if h < 0]

    return {
        "hist_sum": sum(hist_abs),
        "hist_up_sum": sum(hist_up),
        "hist_down_sum": abs(sum(hist_down)),
    }


# ── 笔级别 MACD 力度计算（参考 chanlun-pro query_macd_ld） ─────────────────


def calc_bi_macd_force(
    hist: list[float],
    bi_start_kidx: int,
    bi_end_kidx: int,
    direction: str,
) -> float:
    """计算单笔的 MACD 力度。

    参考 chanlun-pro 的力度比较规则：
    - 向上段取 MACD 红柱（正柱）之和
    - 向下段取 MACD 绿柱（负柱）绝对值之和

    Args:
        hist: MACD 柱状图序列（完整K线的 hist）
        bi_start_kidx: 笔起始分型的 K 线 index
        bi_end_kidx: 笔结束分型的 K 线 index
        direction: 笔方向 \"up\" 或 \"down\"

    Returns:
        MACD 力度值（始终为正数，值越大力度越强）
    """
    if bi_start_kidx > bi_end_kidx or bi_end_kidx >= len(hist):
        return 0.0

    start = max(0, bi_start_kidx)
    end = min(len(hist) - 1, bi_end_kidx)
    hist_slice = hist[start : end + 1]

    if direction == "up":
        # 向上笔：红柱（正柱）之和
        return sum(h for h in hist_slice if h > 0)
    else:
        # 向下笔：绿柱（负柱）绝对值之和
        return abs(sum(h for h in hist_slice if h < 0))


def check_macd_cross_zero(
    hist: list[float],
    start_idx: int,
    end_idx: int,
) -> bool:
    """检查区间内 MACD 柱状图是否穿越零轴（回拉零轴）。

    参考 chanlun-pro judge_macd_back_zero：中枢形成期间 MACD 必须穿过零轴，
    表示中枢有完整的动能转换过程。未穿越零轴的中枢视为弱中枢，过滤其信号。

    Args:
        hist: MACD 柱状图序列
        start_idx: 起始 K 线 index
        end_idx: 结束 K 线 index（含）

    Returns:
        True 如果区间内 hist 发生了正负号变化（穿越零轴）
    """
    if start_idx > end_idx or end_idx >= len(hist):
        return False

    start = max(0, start_idx)
    end = min(len(hist) - 1, end_idx)

    has_positive = any(h > 0 for h in hist[start : end + 1])
    has_negative = any(h < 0 for h in hist[start : end + 1])

    return has_positive and has_negative
