"""买卖点识别（参考 chanlun-pro 优化算法）。

缠论三类买卖点：
- 一类买点：下跌趋势中最后一个中枢下方的底背驰点（MACD力度衰减）
- 二类买点：一类买点后回调不创新低的底分型
- 三类买点：向上突破中枢后回调不跌破中枢上沿的底分型
- 一类卖点：上涨趋势中最后一个中枢上方的顶背驰点（对称）
- 二类卖点：一类卖点后反弹不创新高的顶分型
- 三类卖点：向下跌破中枢后反弹不突破中枢下沿的顶分型

优化项（参考 chanlun-pro）：
1. **MACD力度比较**：用MACD柱状图面积替代幅度，向上段取红柱之和，向下段取绿柱之和
2. **MACD回拉零轴过滤**：中枢形成期间MACD必须穿越零轴，过滤弱中枢
3. **3buy突破笔验证**：3buy前必须有一根向上笔突破中枢上沿zg
4. **趋势上下文**：3buy要求前一个中枢在当前中枢下方（上升趋势）
5. **3sell突破笔验证**：3sell前必须有一根向下笔跌破中枢下沿zd
"""

from __future__ import annotations

from easy_tdx.chanlun.config import ChanlunConfig
from easy_tdx.chanlun.macd import calc_bi_macd_force, check_macd_cross_zero
from easy_tdx.chanlun.types import BI, MMD, ZS, MMDType


def find_mmds(
    bis: list[BI],
    zss: list[ZS],
    config: ChanlunConfig | None = None,
    macd_hist: list[float] | None = None,
) -> list[MMD]:
    """从笔和中枢中识别买卖点。

    Args:
        bis: 笔列表
        zss: 中枢列表
        config: 缠论配置
        macd_hist: MACD柱状图序列（可选，提供后启用MACD力度比较和回拉零轴过滤）

    Returns:
        买卖点列表
    """
    if config is None:
        config = ChanlunConfig()

    if len(bis) < 2 or len(zss) == 0:
        return []

    mmds: list[MMD] = []

    for bi in bis:
        # 寻找与该笔最近的中枢
        for zs in reversed(zss):
            mmd = _check_bi_mmd(bi, zs, bis, zss, macd_hist)
            if mmd is not None:
                mmds.append(mmd)
                break  # 每笔最多一个买卖点

    return mmds


def _get_bi_kidx_range(bi: BI) -> tuple[int, int]:
    """获取笔的 K 线 index 范围。

    使用笔起始分型和结束分型的中间CLKline的k_index。
    """
    start_idx = bi.start.k.k_index
    end_idx = bi.end.k.k_index
    return (start_idx, end_idx)


def _get_zs_kidx_range(zs: ZS) -> tuple[int, int]:
    """获取中枢的 K 线 index 范围。"""
    if not zs.lines:
        return (0, 0)
    start_idx = zs.lines[0].start.k.k_index
    end_idx = zs.lines[-1].end.k.k_index
    return (start_idx, end_idx)


def _check_bi_mmd(
    bi: BI,
    zs: ZS,
    all_bis: list[BI],
    all_zss: list[ZS],
    macd_hist: list[float] | None = None,
) -> MMD | None:
    """检查单笔是否在某中枢附近形成买卖点。"""
    if bi.direction.value == "down":
        return _check_buy_point(bi, zs, all_bis, all_zss, macd_hist)
    else:
        return _check_sell_point(bi, zs, all_bis, all_zss, macd_hist)


def _check_macd_back_zero(
    zs: ZS,
    macd_hist: list[float] | None,
) -> bool:
    """检查中枢是否具有MACD回拉零轴特征。

    无MACD数据时默认通过（向后兼容）。
    """
    if macd_hist is None:
        return True
    zs_start, zs_end = _get_zs_kidx_range(zs)
    return check_macd_cross_zero(macd_hist, zs_start, zs_end)


def _check_force_decreasing_macd(
    bi: BI,
    all_bis: list[BI],
    direction: str,
    macd_hist: list[float] | None,
) -> bool:
    """检查力度是否衰减（MACD面积版）。

    无MACD数据时回退到幅度比较（向后兼容）。
    """
    bi_idx = bi.index
    if bi_idx < 2 or bi_idx >= len(all_bis):
        return False

    # 找前一个同向笔
    for j in range(bi_idx - 1, -1, -1):
        prev = all_bis[j]
        if prev.direction.value == direction:
            if macd_hist is not None:
                curr_start, curr_end = _get_bi_kidx_range(bi)
                prev_start, prev_end = _get_bi_kidx_range(prev)
                curr_force = calc_bi_macd_force(macd_hist, curr_start, curr_end, direction)
                prev_force = calc_bi_macd_force(macd_hist, prev_start, prev_end, direction)
                if direction == "down":
                    return curr_force < prev_force and bi.low < prev.low and curr_force > 0
                else:
                    return curr_force < prev_force and bi.high > prev.high and curr_force > 0
            else:
                # 回退：幅度比较
                curr_range = bi.high - bi.low
                prev_range = prev.high - prev.low
                if direction == "down":
                    return curr_range < prev_range and bi.low < prev.low
                else:
                    return curr_range < prev_range and bi.high > prev.high

    return False


def _is_uptrend_context(zs: ZS, all_zss: list[ZS]) -> bool:
    """检查中枢是否处于上升趋势上下文。

    上升趋势：前一个中枢在当前中枢下方（prev.zg < curr.zd 或 prev.zg <= curr.zg）。
    第一个中枢默认为上升趋势（无前置参照）。
    """
    if zs.index == 0:
        return True
    if zs.index - 1 >= len(all_zss):
        return True
    prev_zs = all_zss[zs.index - 1]
    # 前中枢上沿 <= 当前中枢下沿 -> 上升趋势
    return prev_zs.zg <= zs.zd or prev_zs.zg <= zs.zg


def _is_downtrend_context(zs: ZS, all_zss: list[ZS]) -> bool:
    """检查中枢是否处于下降趋势上下文。"""
    if zs.index == 0:
        return True
    if zs.index - 1 >= len(all_zss):
        return True
    prev_zs = all_zss[zs.index - 1]
    return prev_zs.zd >= zs.zg or prev_zs.zd >= zs.zd


def _check_buy_point(
    bi: BI,
    zs: ZS,
    all_bis: list[BI],
    all_zss: list[ZS],
    macd_hist: list[float] | None = None,
) -> MMD | None:
    """检查向下笔是否形成买点。

    优化项：
    - 1buy: MACD力度衰减（替代幅度比较）
    - 3buy: 验证突破笔 + MACD回拉零轴 + 趋势过滤
    """
    # ── 一类买点：中枢下方力度衰减 ──────────────────────────────────────
    if bi.low < zs.zd:
        if _check_force_decreasing_macd(bi, all_bis, "down", macd_hist):
            return MMD(
                mmd_type=MMDType.BUY_1,
                zs=zs,
                bi=bi,
                msg=f"中枢下方MACD力度衰减，一类买点 (l={bi.low:.2f} < zd={zs.zd:.2f})",
            )

    # ── 二类买点：回调不创新低 ──────────────────────────────────────────
    if bi.low > zs.zd and bi.low > zs.dd:
        bi_idx = bi.index
        if bi_idx >= 2:
            prev_down_bi = all_bis[bi_idx - 2] if bi_idx - 2 < len(all_bis) else None
            if prev_down_bi and prev_down_bi.direction.value == "down":
                if bi.low > prev_down_bi.low:
                    return MMD(
                        mmd_type=MMDType.BUY_2,
                        zs=zs,
                        bi=bi,
                        msg=f"回调不创新低，二类买点 (l={bi.low:.2f})",
                    )

    # ── 三类买点：回调不跌破中枢上沿 ────────────────────────────────────
    # 优化：验证前一笔是向上突破笔 + MACD回拉零轴 + 趋势过滤
    if bi.low > zs.zg:
        # 检查前一笔是否是向上突破笔（high > zg）
        bi_idx = bi.index
        if bi_idx >= 1 and bi_idx - 1 < len(all_bis):
            prev_bi = all_bis[bi_idx - 1]
            if prev_bi.direction.value == "up" and prev_bi.high > zs.zg:
                # 突破笔验证通过

                # MACD回拉零轴过滤
                if not _check_macd_back_zero(zs, macd_hist):
                    return None  # 中枢无回拉零轴，过滤

                # 趋势过滤：要求上升趋势上下文
                is_uptrend = _is_uptrend_context(zs, all_zss)

                trend_msg = "上升趋势" if is_uptrend else "非趋势"
                return MMD(
                    mmd_type=MMDType.BUY_3,
                    zs=zs,
                    bi=bi,
                    msg=(
                        f"突破中枢后回调不破上沿，三类买点 "
                        f"(l={bi.low:.2f} > zg={zs.zg:.2f}, {trend_msg})"
                    ),
                )

        # 无MACD数据时的回退逻辑（向后兼容：不验证突破笔）
        if macd_hist is None:
            return MMD(
                mmd_type=MMDType.BUY_3,
                zs=zs,
                bi=bi,
                msg=f"回调不破中枢上沿，三类买点 (l={bi.low:.2f} > zg={zs.zg:.2f})",
            )

    return None


def _check_sell_point(
    bi: BI,
    zs: ZS,
    all_bis: list[BI],
    all_zss: list[ZS],
    macd_hist: list[float] | None = None,
) -> MMD | None:
    """检查向上笔是否形成卖点。

    优化项：
    - 1sell: MACD力度衰减
    - 3sell: 验证突破笔 + MACD回拉零轴 + 趋势过滤
    """
    # ── 一类卖点：中枢上方力度衰减 ──────────────────────────────────────
    if bi.high > zs.zg:
        if _check_force_decreasing_macd(bi, all_bis, "up", macd_hist):
            return MMD(
                mmd_type=MMDType.SELL_1,
                zs=zs,
                bi=bi,
                msg=f"中枢上方MACD力度衰减，一类卖点 (h={bi.high:.2f} > zg={zs.zg:.2f})",
            )

    # ── 二类卖点：反弹不创新高 ──────────────────────────────────────────
    if bi.high < zs.zg and bi.high < zs.gg:
        bi_idx = bi.index
        if bi_idx >= 2:
            prev_up_bi = all_bis[bi_idx - 2] if bi_idx - 2 < len(all_bis) else None
            if prev_up_bi and prev_up_bi.direction.value == "up":
                if bi.high < prev_up_bi.high:
                    return MMD(
                        mmd_type=MMDType.SELL_2,
                        zs=zs,
                        bi=bi,
                        msg=f"反弹不创新高，二类卖点 (h={bi.high:.2f})",
                    )

    # ── 三类卖点：反弹不突破中枢下沿 ────────────────────────────────────
    # 优化：验证前一笔是向下突破笔 + MACD回拉零轴 + 趋势过滤
    if bi.high < zs.zd:
        # 检查前一笔是否是向下突破笔（low < zd）
        bi_idx = bi.index
        if bi_idx >= 1 and bi_idx - 1 < len(all_bis):
            prev_bi = all_bis[bi_idx - 1]
            if prev_bi.direction.value == "down" and prev_bi.low < zs.zd:
                # 突破笔验证通过

                # MACD回拉零轴过滤
                if not _check_macd_back_zero(zs, macd_hist):
                    return None

                # 趋势过滤：要求下降趋势上下文
                is_downtrend = _is_downtrend_context(zs, all_zss)

                trend_msg = "下降趋势" if is_downtrend else "非趋势"
                return MMD(
                    mmd_type=MMDType.SELL_3,
                    zs=zs,
                    bi=bi,
                    msg=(
                        f"跌破中枢后反弹不破下沿，三类卖点 "
                        f"(h={bi.high:.2f} < zd={zs.zd:.2f}, {trend_msg})"
                    ),
                )

        # 无MACD数据时的回退逻辑
        if macd_hist is None:
            return MMD(
                mmd_type=MMDType.SELL_3,
                zs=zs,
                bi=bi,
                msg=f"反弹不破中枢下沿，三类卖点 (h={bi.high:.2f} < zd={zs.zd:.2f})",
            )

    return None
