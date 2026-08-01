"""增量缠论分析器 — 逐K线推进，彻底消除前瞻偏差。

批量分析器 (ChanlunAnalyser) 一次性处理全量K线，导致5层前瞻偏差：
1. CLKline合并：最后一根CLKline可能随新K线继续合并
2. 分型识别：分型需要3根CLKline，右侧CLKline未确认时分型不成立
3. 笔计算：最后一笔的端点分型可能被更极端的同类型分型替换
4. 中枢计算：最后一个中枢可能继续接收新笔
5. 买卖点识别：``reversed(zss)`` 使用全部中枢（含未来才确认的中枢）

增量分析器逐K线推进，每根K线只使用已确认的结构信息：

1. **CLKline合并**：维护 confirmed + tentative 两段。新K线不与tentative
   合并时，tentative 确认为 confirmed，确认bar = 当前K线index。
2. **分型识别**：新CLKline确认后检查最近3根confirmed CLKline。分型确认bar =
   右侧CLKline的确认bar（比批量分析的"右侧CLKline最后一根K线"更保守、更正确）。
3. **笔计算**：批量find_bis本就是单遍贪心算法，逐分型增量处理结果一致。
4. **中枢计算**：批量find_zss本就是单遍算法，逐笔增量处理结果一致。
   中枢确认bar = 离开笔的确认bar。
5. **买卖点识别**：逐笔检查时只使用 **已确认** 的中枢（确认bar ≤ 当前笔确认bar），
   彻底消除 ``reversed(zss)`` 的前瞻偏差。

最终产出 ChanlunResult，其中 ``signals_by_bar`` 字段记录每个确认bar上新确认的
MMD信号。策略检测到此字段非空时使用增量信号，否则回退批量逻辑。
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from easy_tdx.chanlun.analyser import ChanlunResult, _df_to_klines
from easy_tdx.chanlun.beichi import check_bi_beichi
from easy_tdx.chanlun.bi import _can_form_bi
from easy_tdx.chanlun.config import ChanlunConfig
from easy_tdx.chanlun.kline_merge import _is_included, _to_clkline
from easy_tdx.chanlun.macd import calc_macd
from easy_tdx.chanlun.mmd import _check_bi_mmd
from easy_tdx.chanlun.types import BI, FX, MMD, ZS, CLKline, Direction, FXType, Kline
from easy_tdx.chanlun.xd import find_xds


class IncrementalAnalyser:
    """增量缠论分析器。

    逐K线推进，每根K线只使用已确认的结构信息，彻底消除前瞻偏差。

    用法::

        analyser = IncrementalAnalyser("SZ000001", "DAILY")
        result = analyser.process_klines(df)
        # result.signals_by_bar: {bar_index: [MMD, ...]}
    """

    def __init__(
        self,
        code: str = "",
        frequency: str = "",
        config: ChanlunConfig | None = None,
    ) -> None:
        self._code = code
        self._frequency = frequency
        self._config = config or ChanlunConfig()

        # ── K线 ──────────────────────────────────────────────────────────
        self._klines: list[Kline] = []

        # ── CLKline合并 ──────────────────────────────────────────────────
        self._confirmed_cklines: list[CLKline] = []
        self._tentative_ckline: CLKline | None = None
        # ckline.index -> 确认时的K线index
        self._ckline_confirm_bar: dict[int, int] = {}

        # ── 分型 ──────────────────────────────────────────────────────────
        self._fractals: list[FX] = []
        # fx.index -> 确认时的K线index
        self._fractal_confirm_bar: dict[int, int] = {}
        # 上次检查分型时的右侧CLKline index（避免重复检查）
        self._last_fractal_check_ckline_idx: int = -1

        # ── 笔 ──────────────────────────────────────────────────────────
        self._bis: list[BI] = []
        # bi.index -> 确认时的K线index
        self._bi_confirm_bar: dict[int, int] = {}
        # 笔计算状态（与批量find_bis算法一致）
        self._start_fx: FX | None = None
        self._pending_opposite: FX | None = None

        # ── 中枢 ──────────────────────────────────────────────────────────
        self._zss: list[ZS] = []  # 已确认（已关闭）的中枢
        # zs.index -> 确认时的K线index（离开笔的确认bar）
        self._zs_confirm_bar: dict[int, int] = {}
        self._current_zs: ZS | None = None  # 当前正在构建的中枢
        self._zs_pending: list[BI] = []

        # ── 买卖点 ────────────────────────────────────────────────────────
        self._mmds: list[MMD] = []
        # key=确认bar的K线index, value=该bar新确认的MMD列表
        self._signals_by_bar: dict[int, list[MMD]] = {}

        # ── MACD 数据（预计算，用于力度比较和回拉零轴过滤） ──────────────
        self._macd_hist: list[float] = []

        # ── 结果 ──────────────────────────────────────────────────────────
        self._result = ChanlunResult(code=code, frequency=frequency)

    @property
    def result(self) -> ChanlunResult:
        return self._result

    def process_klines(self, df: pd.DataFrame) -> ChanlunResult:
        """处理K线DataFrame，逐K线增量推进。

        内部逐K线调用 ``_add_kline``，最终构建并返回 ChanlunResult。
        结果中 ``signals_by_bar`` 字段记录每个确认bar上新确认的MMD信号。
        """
        klines = _df_to_klines(df)
        # 预计算 MACD（K线级别的 EMA 不会因后续结构变化而改变，
        # 可安全预计算，用于力度比较和回拉零轴过滤）
        closes = [k.close for k in klines]
        macd_data = calc_macd(
            closes,
            self._config.macd_fast,
            self._config.macd_slow,
            self._config.macd_signal,
        )
        self._macd_hist = macd_data.get("hist", [])
        for kline in klines:
            self._add_kline(kline)
        self._build_result()
        return self._result

    # ── 核心：逐K线增量处理 ──────────────────────────────────────────────

    def _add_kline(self, kline: Kline) -> None:
        """处理一根K线，级联更新所有结构层。

        级联链：CLKline确认 → 分型检查 → 笔更新 → 中枢更新 + MMD检查。
        每层只在上一层有新确认结构时才触发，大部分K线只更新tentative CLKline。
        """
        self._klines.append(kline)

        # Layer 1: CLKline合并
        newly_confirmed_ck = self._update_cklines(kline)
        if newly_confirmed_ck is None:
            return

        # Layer 2: 分型识别（新CLKline确认后检查最近3根confirmed CLKline）
        newly_confirmed_fx = self._check_fractals(kline.index)
        if newly_confirmed_fx is None:
            return

        # Layer 3: 笔计算（新分型确认后增量更新笔）
        newly_confirmed_bi = self._update_bis(newly_confirmed_fx, kline.index)
        if newly_confirmed_bi is None:
            return

        # Layer 4: 中枢计算（新笔确认后增量更新中枢）
        self._update_zss(newly_confirmed_bi, kline.index)

        # Layer 5: 买卖点识别（新笔确认后检查MMD）
        self._check_mmds(newly_confirmed_bi, kline.index)

    # ── Layer 1: CLKline合并 ────────────────────────────────────────────

    def _update_cklines(self, kline: Kline) -> CLKline | None:
        """增量合并K线。

        新K线与tentative CLKline比较：
        - 有包含关系 → 合并（更新tentative的high/low/open/close等）
        - 无包含关系 → tentative确认为confirmed，新K线成为新tentative

        Returns:
            新确认的CLKline（如果tentative被确认），否则None
        """
        if self._tentative_ckline is None:
            self._tentative_ckline = _to_clkline(kline, index=0)
            return None

        candidate = _to_clkline(kline, index=0)

        if _is_included(self._tentative_ckline, candidate) or _is_included(
            candidate, self._tentative_ckline
        ):
            # 包含关系：合并到tentative
            direction = self._get_merge_direction()
            if direction == "up":
                merged_high = max(self._tentative_ckline.high, candidate.high)
                merged_low = max(self._tentative_ckline.low, candidate.low)
            else:
                merged_high = min(self._tentative_ckline.high, candidate.high)
                merged_low = min(self._tentative_ckline.low, candidate.low)

            self._tentative_ckline.high = merged_high
            self._tentative_ckline.low = merged_low
            self._tentative_ckline.k_index = kline.index
            self._tentative_ckline.date = kline.date
            self._tentative_ckline.merged_count += 1
            self._tentative_ckline.klines.append(kline)
            self._tentative_ckline.open = kline.open
            self._tentative_ckline.close = kline.close
            self._tentative_ckline.amount += kline.amount
            self._tentative_ckline.direction = direction
            return None

        # 无包含关系：确认tentative为confirmed
        confirmed = self._tentative_ckline
        confirmed.index = len(self._confirmed_cklines)
        self._confirmed_cklines.append(confirmed)
        # 确认bar = 当前K线index（第一根不与tentative合并的K线）
        self._ckline_confirm_bar[confirmed.index] = kline.index

        # 新K线成为新tentative
        self._tentative_ckline = _to_clkline(kline, index=0)
        return confirmed

    def _get_merge_direction(self) -> str:
        """获取合并方向（与批量merge_klines逻辑一致）。

        向上合并取高高，向下合并取低低。方向由tentative CLKline与
        前一根confirmed CLKline的high比较决定。
        """
        if self._tentative_ckline is None:
            return "up"
        if len(self._confirmed_cklines) >= 1:
            prev = self._confirmed_cklines[-1]
            return "up" if self._tentative_ckline.high > prev.high else "down"
        # 只有第一根CLKline时，根据阴阳线判断
        return (
            "up"
            if self._tentative_ckline.close >= self._tentative_ckline.open
            else "down"
        )

    # ── Layer 2: 分型识别 ────────────────────────────────────────────────

    def _check_fractals(self, confirm_bar: int) -> FX | None:
        """检查新确认的CLKline是否产生新分型。

        当新CLKline确认后，检查最近3根confirmed CLKline [left, mid, right]。
        分型确认bar = 右侧CLKline的确认bar（即当前K线index）。

        与批量find_fractals的区别：批量分析在所有CLKline最终确定后统一扫描，
        而增量分析在每根CLKline确认时只检查一个窗口，结果一致（每个窗口只检查一次）。
        """
        if len(self._confirmed_cklines) < 3:
            return None

        right = self._confirmed_cklines[-1]

        # 避免重复检查同一窗口
        if right.index <= self._last_fractal_check_ckline_idx:
            return None
        self._last_fractal_check_ckline_idx = right.index

        left = self._confirmed_cklines[-3]
        mid = self._confirmed_cklines[-2]

        if self._config.fx_strict:
            is_ding = mid.high > left.high and mid.high > right.high
            is_di = mid.low < left.low and mid.low < right.low
        else:
            is_ding = mid.high >= left.high and mid.high >= right.high
            is_di = mid.low <= left.low and mid.low <= right.low

        if is_ding and is_di:
            return None  # 十字星，跳过
        elif is_ding:
            fx = FX(
                fx_type=FXType.DING,
                k=mid,
                klines=[left, mid, right],
                val=mid.high,
                index=len(self._fractals),
                done=True,
            )
            self._fractals.append(fx)
            self._fractal_confirm_bar[fx.index] = confirm_bar
            return fx
        elif is_di:
            fx = FX(
                fx_type=FXType.DI,
                k=mid,
                klines=[left, mid, right],
                val=mid.low,
                index=len(self._fractals),
                done=True,
            )
            self._fractals.append(fx)
            self._fractal_confirm_bar[fx.index] = confirm_bar
            return fx

        return None

    # ── Layer 3: 笔计算 ────────────────────────────────────────────────

    def _update_bis(self, new_fx: FX, confirm_bar: int) -> BI | None:
        """增量更新笔。

        与批量find_bis算法逻辑完全一致，只是逐分型处理：
        1. 同类型分型：取更极端的替换start_fx（有pending_opposite时不替换）
        2. 异类型分型：检查gap，满足则成笔，否则记为pending_opposite

        Returns:
            新确认的笔（如果成笔），否则None
        """
        if self._start_fx is None:
            self._start_fx = new_fx
            return None

        current_fx = new_fx

        # 同类型分型
        if current_fx.fx_type == self._start_fx.fx_type:
            if self._pending_opposite is not None:
                # 有pending_opposite时不替换start_fx（避免分型陷阱）
                return None
            if (
                self._start_fx.fx_type == FXType.DING
                and current_fx.val > self._start_fx.val
            ):
                self._start_fx = current_fx
            elif (
                self._start_fx.fx_type == FXType.DI
                and current_fx.val < self._start_fx.val
            ):
                self._start_fx = current_fx
            return None

        # 异类型分型，检查是否可以成笔
        if _can_form_bi(self._start_fx, current_fx, self._config):
            direction = (
                Direction.UP if self._start_fx.fx_type == FXType.DI else Direction.DOWN
            )
            high = max(self._start_fx.val, current_fx.val)
            low = min(self._start_fx.val, current_fx.val)

            bi = BI(
                start=self._start_fx,
                end=current_fx,
                direction=direction,
                index=len(self._bis),
                high=high,
                low=low,
            )
            self._bis.append(bi)
            self._bi_confirm_bar[bi.index] = confirm_bar
            self._start_fx = current_fx
            self._pending_opposite = None
            return bi

        # gap不够，记为pending
        self._pending_opposite = current_fx
        return None

    # ── Layer 4: 中枢计算 ────────────────────────────────────────────────

    def _update_zss(self, new_bi: BI, confirm_bar: int) -> ZS | None:
        """增量更新中枢。

        与批量find_zss算法逻辑完全一致，只是逐笔处理：
        1. 无当前中枢 → 用新笔初始化
        2. 新笔与当前中枢有重叠 → 加入中枢，更新zg/zd/gg/dd
        3. 新笔与当前中枢无重叠 → 关闭当前中枢（如果够3笔），新笔初始化新中枢

        Returns:
            新关闭的中枢（如果有），否则None
        """
        if self._current_zs is None:
            self._zs_pending.append(new_bi)
            while len(self._zs_pending) >= self._config.zs_min_lines:
                candidate = self._try_start_zs(
                    self._zs_pending[: self._config.zs_min_lines]
                )
                if candidate is not None:
                    self._current_zs = candidate
                    self._zs_pending.clear()
                    break
                self._zs_pending.pop(0)
            return None

        overlap_high = min(self._current_zs.zg, new_bi.high)
        overlap_low = max(self._current_zs.zd, new_bi.low)

        if overlap_high > overlap_low:
            # 有重叠，加入中枢
            self._current_zs.add_line(new_bi)
            self._current_zs.zg = overlap_high
            self._current_zs.zd = overlap_low
            self._current_zs.gg = max(self._current_zs.gg, new_bi.high)
            self._current_zs.dd = min(self._current_zs.dd, new_bi.low)
            self._current_zs.end = new_bi.end
            return None

        # 无重叠，关闭当前中枢
        closed_zs: ZS | None = None
        if self._current_zs.line_count >= self._config.zs_min_lines:
            self._current_zs.done = True
            self._zss.append(self._current_zs)
            self._zs_confirm_bar[self._current_zs.index] = confirm_bar
            closed_zs = self._current_zs

        carry = (
            cast(list[BI], self._current_zs.lines[-(self._config.zs_min_lines - 1) :])
            if self._config.zs_min_lines > 1
            else []
        )
        self._current_zs = None
        self._zs_pending = list(carry) + [new_bi]
        while len(self._zs_pending) >= self._config.zs_min_lines:
            candidate = self._try_start_zs(
                self._zs_pending[: self._config.zs_min_lines]
            )
            if candidate is not None:
                self._current_zs = candidate
                self._zs_pending.clear()
                break
            self._zs_pending.pop(0)
        return closed_zs

    def _try_start_zs(self, lines: list[BI]) -> ZS | None:
        if len(lines) < self._config.zs_min_lines:
            return None
        overlap_high = min(l.high for l in lines)
        overlap_low = max(l.low for l in lines)
        if overlap_high <= overlap_low:
            return None
        return ZS(
            lines=list(lines),
            zg=overlap_high,
            zd=overlap_low,
            gg=max(l.high for l in lines),
            dd=min(l.low for l in lines),
            start=lines[0].start,
            end=lines[-1].end,
            index=len(self._zss),
        )

    # ── Layer 5: 买卖点识别 ──────────────────────────────────────────────

    def _check_mmds(self, new_bi: BI, confirm_bar: int) -> None:
        """检查新确认的笔是否产生买卖点。

        **核心修复**：只使用已确认（已关闭）的中枢，且中枢确认bar ≤ 当前笔确认bar。
        这彻底消除了批量find_mmds中 ``reversed(zss)`` 使用未来中枢的前瞻偏差。

        **优化**：传入 MACD 柱状图和全部已确认中枢列表，
        启用 MACD 力度比较、回拉零轴过滤和趋势上下文判断。

        对每个新确认的笔，从最近到最远遍历已确认中枢，找到第一个匹配的买卖点。
        """
        # 只使用确认bar <= 当前笔确认bar 的已关闭中枢
        confirmed_zss = [
            zs
            for zs in self._zss
            if self._zs_confirm_bar.get(zs.index, float("inf")) <= confirm_bar
        ]

        if not confirmed_zss:
            return

        # 从最近到最远遍历，找到第一个匹配的买卖点
        for zs in reversed(confirmed_zss):
            mmd = _check_bi_mmd(
                new_bi, zs, self._bis, confirmed_zss, self._macd_hist
            )
            if mmd is not None:
                self._mmds.append(mmd)
                self._signals_by_bar.setdefault(confirm_bar, []).append(mmd)
                break  # 每笔最多一个买卖点

    # ── 构建最终结果 ──────────────────────────────────────────────────────

    def _build_result(self) -> None:
        """构建最终ChanlunResult。

        产出与批量分析器兼容的结果（包含所有结构），额外填充 signals_by_bar。
        """
        self._result.klines = self._klines

        # CLKlines: confirmed + tentative
        all_cklines = list(self._confirmed_cklines)
        if self._tentative_ckline is not None:
            self._tentative_ckline.index = len(all_cklines)
            all_cklines.append(self._tentative_ckline)
        self._result.cklines = all_cklines

        self._result.fractals = self._fractals
        self._result.bis = self._bis

        # ZSs: confirmed (closed) + current (if enough lines)
        all_zss = list(self._zss)
        if (
            self._current_zs is not None
            and self._current_zs.line_count >= self._config.zs_min_lines
        ):
            self._current_zs.done = False  # 最后一根K线未确定
            all_zss.append(self._current_zs)
        self._result.zss = all_zss

        self._result.mmds = self._mmds

        # MACD（使用预计算的数据）
        self._result.macd = {
            "dif": [],
            "dea": [],
            "hist": self._macd_hist,
        }
        # 补全 dif 和 dea
        closes = [k.close for k in self._klines]
        full_macd = calc_macd(
            closes,
            self._config.macd_fast,
            self._config.macd_slow,
            self._config.macd_signal,
        )
        self._result.macd = full_macd

        # 线段（从confirmed bis计算）
        self._result.xds = find_xds(self._bis, self._config)

        # 背驰（从confirmed bis和all_zss计算，传入MACD启用面积比较）
        self._result.bcs = check_bi_beichi(
            self._bis, all_zss, self._config, self._macd_hist
        )

        # 增量信号映射
        self._result.signals_by_bar = self._signals_by_bar


# ── 验证工具 ──────────────────────────────────────────────────────────────


def compare_batch_vs_incremental(
    df: pd.DataFrame,
    config: ChanlunConfig | None = None,
) -> dict[str, Any]:
    """对比批量分析与增量分析的结果差异。

    用于验证增量分析器的正确性。对于历史结构（远离末尾的部分），
    两者应完全一致。差异只应出现在最后几根结构（tentative部分）。

    Returns:
        包含各结构数量和差异信息的字典
    """
    from easy_tdx.chanlun.analyser import ChanlunAnalyser

    config = config or ChanlunConfig()

    # 批量分析
    batch_analyser = ChanlunAnalyser(config=config)
    batch_result = batch_analyser.process_klines(df)

    # 增量分析
    incr_analyser = IncrementalAnalyser(config=config)
    incr_result = incr_analyser.process_klines(df)

    return {
        "cklines": {
            "batch": len(batch_result.cklines),
            "incremental": len(incr_result.cklines),
            "diff": len(batch_result.cklines) - len(incr_result.cklines),
        },
        "fractals": {
            "batch": len(batch_result.fractals),
            "incremental": len(incr_result.fractals),
            "diff": len(batch_result.fractals) - len(incr_result.fractals),
        },
        "bis": {
            "batch": len(batch_result.bis),
            "incremental": len(incr_result.bis),
            "diff": len(batch_result.bis) - len(incr_result.bis),
        },
        "zss": {
            "batch": len(batch_result.zss),
            "incremental": len(incr_result.zss),
            "diff": len(batch_result.zss) - len(incr_result.zss),
        },
        "mmds": {
            "batch": len(batch_result.mmds),
            "incremental": len(incr_result.mmds),
            "diff": len(batch_result.mmds) - len(incr_result.mmds),
        },
        "signals_by_bar_count": len(incr_result.signals_by_bar),
        "batch_mmd_types": [m.mmd_type.value for m in batch_result.mmds],
        "incr_mmd_types": [m.mmd_type.value for m in incr_result.mmds],
    }
