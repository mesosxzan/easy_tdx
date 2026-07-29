"""背驰判断。

背驰类型：
- 笔背驰：相邻同向笔的力度比较（幅度或 MACD 面积减小）
- 盘整背驰：中枢内最后一笔力度小于进入中枢的第一笔
- 趋势背驰：两个同向中枢之间，离开中枢的笔力度减小
"""

from __future__ import annotations

from easy_tdx.chanlun.config import ChanlunConfig
from easy_tdx.chanlun.macd import calc_bi_macd_force
from easy_tdx.chanlun.types import BC, BI, XD, ZS, BCType


def check_bi_beichi(
    bis: list[BI],
    zss: list[ZS],
    config: ChanlunConfig | None = None,
    macd_hist: list[float] | None = None,
) -> list[BC]:
    """检查笔级别的背驰。

    算法：
    1. 笔背驰：比较相邻同向笔的MACD力度（后 < 前 = 背驰）
    2. 盘整背驰：中枢内最后一笔与进入笔比较
    3. 趋势背驰：连续两个同向中枢，离开力度减小

    Args:
        bis: 笔列表
        zss: 中枢列表
        config: 缠论配置
        macd_hist: MACD柱状图序列（可选，提供后用MACD面积替代幅度）

    Returns:
        背驰列表
    """
    if config is None:
        config = ChanlunConfig()

    if len(bis) < 2:
        return []

    bcs: list[BC] = []

    # 1. 笔背驰检查
    bcs.extend(_check_bi_level_beichi(bis, macd_hist))

    # 2. 盘整背驰检查
    if len(zss) > 0:
        bcs.extend(_check_pz_beichi(bis, zss, macd_hist))

    # 3. 趋势背驰检查
    if len(zss) >= 2:
        bcs.extend(_check_qs_beichi(bis, zss, macd_hist))

    return bcs


def _check_bi_level_beichi(
    bis: list[BI],
    macd_hist: list[float] | None = None,
) -> list[BC]:
    """检查笔级别的力度背驰。"""
    bcs: list[BC] = []

    # 按方向分组比较
    for i in range(1, len(bis)):
        curr = bis[i]
        # 向前找最近的同向笔
        for j in range(i - 1, -1, -1):
            prev = bis[j]
            if prev.direction == curr.direction:
                curr_force = _calc_bi_force(curr, macd_hist)
                prev_force = _calc_bi_force(prev, macd_hist)

                # 力度衰减 = 背驰
                if curr_force < prev_force and curr_force > 0:
                    bcs.append(
                        BC(
                            bc_type=BCType.BI,
                            bc=True,
                            zs=None,
                            curr=curr,
                            prev=prev,
                            msg=(
                                f"笔背驰: 笔[{curr.index}] 力度={curr_force:.2f} "
                                f"< 笔[{prev.index}] 力度={prev_force:.2f}"
                            ),
                        )
                    )
                break  # 只比较最近一个同向笔

    return bcs


def _check_pz_beichi(
    bis: list[BI],
    zss: list[ZS],
    macd_hist: list[float] | None = None,
) -> list[BC]:
    """检查盘整背驰。"""
    bcs: list[BC] = []

    for zs in zss:
        if zs.line_count < 3:
            continue

        # 中枢内最后一笔 vs 进入中枢的第一笔
        first_bi = zs.lines[0]
        last_bi = zs.lines[-1]

        if first_bi.direction == last_bi.direction:
            first_force = _calc_bi_force(first_bi, macd_hist)
            last_force = _calc_bi_force(last_bi, macd_hist)

            if last_force < first_force and last_force > 0:
                bcs.append(
                    BC(
                        bc_type=BCType.PZ,
                        bc=True,
                        zs=zs,
                        curr=last_bi,
                        prev=first_bi,
                        msg=(
                            f"盘整背驰: 中枢[{zs.index}] 内末笔力度={last_force:.2f} "
                            f"< 首笔力度={first_force:.2f}"
                        ),
                    )
                )

    return bcs


def _check_qs_beichi(
    bis: list[BI],
    zss: list[ZS],
    macd_hist: list[float] | None = None,
) -> list[BC]:
    """检查趋势背驰。"""
    bcs: list[BC] = []

    for i in range(1, len(zss)):
        prev_zs = zss[i - 1]
        curr_zs = zss[i]

        # 判断两个中枢是否形成趋势（同向排列）
        if prev_zs.zg >= curr_zs.zg and prev_zs.zd >= curr_zs.zd:
            # 向下趋势
            prev_exit_force = _calc_bi_force(prev_zs.lines[-1], macd_hist)
            curr_exit_force = _calc_bi_force(curr_zs.lines[-1], macd_hist)

            if curr_exit_force < prev_exit_force and curr_exit_force > 0:
                bcs.append(
                    BC(
                        bc_type=BCType.QS,
                        bc=True,
                        zs=curr_zs,
                        curr=curr_zs.lines[-1],
                        prev=prev_zs.lines[-1],
                        msg=(
                            f"趋势背驰(下): 中枢[{curr_zs.index}] 离开力度={curr_exit_force:.2f} "
                            f"< 中枢[{prev_zs.index}] 离开力度={prev_exit_force:.2f}"
                        ),
                    )
                )
        elif prev_zs.zg <= curr_zs.zg and prev_zs.zd <= curr_zs.zd:
            # 向上趋势
            prev_exit_force = _calc_bi_force(prev_zs.lines[-1], macd_hist)
            curr_exit_force = _calc_bi_force(curr_zs.lines[-1], macd_hist)

            if curr_exit_force < prev_exit_force and curr_exit_force > 0:
                bcs.append(
                    BC(
                        bc_type=BCType.QS,
                        bc=True,
                        zs=curr_zs,
                        curr=curr_zs.lines[-1],
                        prev=prev_zs.lines[-1],
                        msg=(
                            f"趋势背驰(上): 中枢[{curr_zs.index}] 离开力度={curr_exit_force:.2f} "
                            f"< 中枢[{prev_zs.index}] 离开力度={prev_exit_force:.2f}"
                        ),
                    )
                )

    return bcs


def _calc_bi_force(
    bi: BI | XD,
    macd_hist: list[float] | None = None,
) -> float:
    """计算笔的力度。

    优先使用 MACD 柱状图面积（参考 chanlun-pro query_macd_ld）：
    - 向上笔：红柱（正柱）之和
    - 向下笔：绿柱（负柱）绝对值之和

    无 MACD 数据时回退到幅度（向后兼容）。
    """
    if macd_hist is not None:
        direction = bi.direction.value
        start_idx = bi.start.k.k_index
        end_idx = bi.end.k.k_index
        return calc_bi_macd_force(macd_hist, start_idx, end_idx, direction)
    return abs(bi.high - bi.low)
