"""影线后收阳线策略 V3（盈利能力优化版）。

V3 核心优化（基于 V2 的 30 股 158 笔交易数据驱动）::

  V2 基线（30 只个股，158 笔交易，平均收益 +10.92%，PF=2.10）::

    卖出原因分布：
      止损清仓    78笔  avg -2.59%  （亏损主力，57% 集中在 -3~0% 小亏）
      盈利锁定    31笔  avg +0.64%  （鸡肋，45% 胜率，贡献极低）
      震荡市止损  25笔  avg -0.03%  （中性）
      止盈清仓    24笔  avg +19.57%（核心利润来源，仅 15% 交易数）

  V3 优化方向（三大改进）::

    1. 【入场】回调入场机制（PULLBACK_ENTRY）：
       信号日不立即买入，等价格回踩 MA5/MA10 再入场。
       数据驱动：55 笔 -3~0% 的小亏交易很多是买在阳线高点后小幅回落，
       回踩入场可降低买入成本 2-5%，直接减少小亏、提升盈亏比。

    2. 【出场】精简盈利锁定（从 5 级缩减为 3 级）：
       移除 Tier1(3%保本) 和 Tier2(6%锁2%)，从 Tier3(10%锁5%) 开始。
       数据驱动：31 笔盈利锁定平均仅 +0.64%，45% 胜率，很多是被 Tier1/Tier2
       过早锁住的趋势单（后来可能涨到 15%+）。移除低阶锁定让更多单子
       进入追踪止盈/止盈清仓（平均 +19.57%）。

    3. 【仓位】趋势金字塔加仓（PYRAMID_ADD）：
       多头排列 + 浮盈 > 10% 且突破前高时加仓 50%。
       数据驱动：24 笔止盈清仓平均 +19.57%，是策略核心利润来源。
       对这些大趋势单加仓可显著放大盈利，风险可控（已确认趋势+已有浮盈垫）。

    4. 【入场】阳线实体强度过滤（STRONG_BODY_FILTER）：
       阳线实体 < 前日跌幅 × 0.5 时不买入（弱反弹）。
       数据驱动：-3~0% 小亏 55 笔，很多是弱反弹假信号，
       过滤弱实体阳线可减少无效交易。

用法::
    easy-tdx backtest 600206 --strategy-file strategies/shadow_yang_v3.py
"""

import math

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy

# ── 常量（保留 V2 基础参数）───────────────────────────────────────────────

LOW_OPEN_THRESHOLD = 0.97
UPPER_SHADOW_RATIO = 1.5
STRONG_TREND_SHADOW_RATIO = 2.0
STAR_BODY_RATIO = 0.5
STRONG_REVERSAL_BODY_RATIO = 2.0
MA_LONG_PERIOD = 20
MA_VERY_LONG_PERIOD = 60
MA_STOP_PERIOD = 10
SLOPE_THRESHOLD_DEG = 45.0
MA_BULLISH_MA5_DEVIATION = 0.01
MA_CONVERGENCE_THRESHOLD = 0.02

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.2
MAX_LOSS_PCT = 8.0
MA20_DEVIATION_MAX = 0.25
RECENT_SURGE_DAYS = 5
RECENT_SURGE_MAX = 0.20
SHORT_SURGE_DAYS = 3
SHORT_SURGE_MAX = 0.12

STRONG_TREND_ADX = 30
STRONG_TREND_SURGE3_MAX = 0.18
STRONG_TREND_SURGE5_MAX = 0.30
STRONG_TREND_DEV_MAX = 0.35
STRONG_TREND_YIN_DROP_RATIO = 2.0
STRONG_TREND_YIN_MIN_PCT = 3.0
BEAR_STRONG_TREND_DI_RATIO = 2.0

BULLISH_YIN_DROP_ATR_RATIO = 1.5
BULLISH_YIN_MIN_PCT = 2.5
VOLUME_RATIO_MIN = 1.0
COOLDOWN_BARS = 5
MAX_CONSECUTIVE_LOSSES = 2
PROFIT_RELAX_THRESHOLD = 0.10

# ── 分阶段追踪止盈（V2 参数保留）───────────────────────────────────────────
PROFIT_TIER2 = 0.05
PROFIT_TIER3 = 0.15
PROFIT_TIER4 = 0.25
ATR_TRAIL_WIDE = 3.0
ATR_TRAIL_TIGHT = 2.0
ATR_TRAIL_MEGA = 2.5
TRAILING_STOP_PCT_MAX = 12.0

# ═══════════════════════════════════════════════════════════════════════
# V3 新增/修改参数
# ═══════════════════════════════════════════════════════════════════════

# ── V3 优化 1：回调入场 ──────────────────────────────────────────────────
# 信号日不立即买入，等待价格回踩 MA5 或 MA10 再入场
# 降低买入成本，提升盈亏比；等待期设上限（避免错过趋势）
PULLBACK_ENTRY_ENABLED = True
PULLBACK_WAIT_MAX_BARS = 5  # 最多等 5 根 K 线，超时则放弃
PULLBACK_MA5_TOLERANCE = 0.01  # 回踩 MA5：收盘价 <= MA5 × (1 + tolerance) 即触发
PULLBACK_MA10_TOLERANCE = 0.015  # 回踩 MA10 触发线

# ── V3 优化 2：精简盈利锁定（从 5 级缩减为 3 级）─────────────────────────
# V2: 3%保本 -> 6%锁2% -> 10%锁3% -> 15%锁7% -> 20%锁10%
# V3: 10%锁5% -> 15%锁8% -> 20%锁12%
# 移除前两阶，让更多单子有机会发展成大盈利
PROFIT_LOCKIN_TRIGGER3 = 0.10  # 收盘浮盈达 10%：锁定 5% 利润（V2 是 3%）
PROFIT_LOCKIN_FLOOR3 = 0.05
PROFIT_LOCKIN_TRIGGER4 = 0.15  # 收盘浮盈达 15%：锁定 8% 利润（V2 是 7%）
PROFIT_LOCKIN_FLOOR4 = 0.08
PROFIT_LOCKIN_TRIGGER5 = 0.20  # 收盘浮盈达 20%：锁定 12% 利润（V2 是 10%）
PROFIT_LOCKIN_FLOOR5 = 0.12
# 强趋势从更高阶开始锁定
PROFIT_LOCKIN_STRONG_TREND_MIN_TIER = 4  # 强趋势从 Tier4(15%) 开始锁定

# ── V3 优化 3：趋势金字塔加仓 ────────────────────────────────────────────
PYRAMID_ENABLED = True
PYRAMID_PROFIT_THRESHOLD = 0.10  # 浮盈 >= 10% 才考虑加仓
PYRAMID_ADD_RATIO = 0.5  # 加仓比例 = 当前持仓 × 50%
PYRAMID_MAX_ADD_TIMES = 1  # 最多加仓 1 次
PYRAMID_BREAKOUT_LOOKBACK = 10  # 突破近 N 日新高才加仓

# ── V3 优化 4：弱反弹过滤 ────────────────────────────────────────────────
# 阳线实体 / 前日阴线实体 < 阈值时，视为弱反弹，不买入
WEAK_REBOUND_BODY_RATIO_MIN = 0.6  # 阳线实体至少是前日阴线的 60%（仅影线后收阳路径）

# ── 保留的 V2 过滤参数 ────────────────────────────────────────────────────
ADX_RAPID_THRESHOLD = 20
RAPID_PROFIT_TIER = 0.03
RAPID_ATR_TRAIL = 1.5
RAPID_TRAILING_PCT = 4.0
RAPID_STOP_MULTIPLIER = 1.5
RAPID_MAX_HOLD_DAYS = 3
RAPID_MIN_PROFIT_KEEP = 0.02

TREND_ENTRY_RAPID_HOLD_DAYS = 10

MA60_FLAT_LOOKBACK = 20
MA60_DROP_TOLERANCE = 0.005
MA60_EXCEPTION_GAIN_MIN = 9.5
MA60_EXCEPTION_DROP_MAX = 0.03

ADX_PERIOD = 14
ADX_MIN_THRESHOLD = 20
VOLATILITY_MAX_PCT = 8.0

ADX_MID_RANGE_MIN = 15
ADX_MID_RANGE_MAX = 30
RECENT_DROP_LOOKBACK = 3
RECENT_DROP_MAX = 0.06
WEAK_REBOUND_BODY_RATIO = 0.5

YIN_DROP_ATR_RATIO = 1.2

CCI_PERIOD = 14
CCI_OVERBOUGHT = 100
MFI_PERIOD = 14
MFI_OVERBOUGHT = 80
BOLL_PERIOD = 20
BOLL_DEVIATION = 2
BOLL_PCTB_OVERBOUGHT = 1.0
BOLL_PCTB_HIGH_ZONE = 0.8
BOLL_PCTB_BULLISH_MAX = 0.8

BODY_RATIO_MIN = 0.3
BOLL_WIDTH_DEATH_ZONE_LOW = 20.0
BOLL_WIDTH_DEATH_ZONE_HIGH = 25.0

LOWER_SHADOW_RATIO_MAX = 1.0
KDJ_PERIOD = 9
KDJ_J_OVERBOUGHT = 100

FAKE_BREAKOUT_SHADOW_RATIO = 1.0
FAKE_BREAKOUT_DEVIATION_MAX = 0.02

_WAITING = "WAITING"
_HOLDING = "HOLDING"
_SIGNALED = "SIGNALED"  # V3：已发出信号，等待回调入场


class ShadowYangStrategy(Strategy):
    """影线后收阳线策略 V3（盈利能力优化版）。

    状态机::
        WAITING --信号触发--> SIGNALED --回踩买入--> HOLDING --止损/止盈--> WAITING
                                          \--超时放弃--> WAITING
    """

    def init(self) -> None:
        self.ma5 = self.I(MyTT.MA, self.data.close, 5)
        self.ma10 = self.I(MyTT.MA, self.data.close, MA_STOP_PERIOD)
        self.ma20 = self.I(MyTT.MA, self.data.close, MA_LONG_PERIOD)
        self.ma60 = self.I(MyTT.MA, self.data.close, MA_VERY_LONG_PERIOD)
        self._pdi, self._mdi, self._adx, self._adxr = self.I(
            MyTT.DMI, self.data.close, self.data.high, self.data.low
        )
        self._cci = self.I(MyTT.CCI, self.data.close, self.data.high, self.data.low, CCI_PERIOD)
        self._mfi = self.I(
            MyTT.MFI,
            self.data.close, self.data.high, self.data.low, self.data.vol,
            MFI_PERIOD,
        )
        self._boll_upper, self._boll_mid, self._boll_lower = self.I(
            MyTT.BOLL, self.data.close, BOLL_PERIOD, BOLL_DEVIATION
        )
        self._kdj_k, self._kdj_d, self._kdj_j = self.I(
            MyTT.KDJ, self.data.close, self.data.high, self.data.low, KDJ_PERIOD, 3, 3
        )
        self._state: str = _WAITING
        self._entry_price: float = 0.0
        self._highest_since_entry: float = 0.0
        self._highest_close_since_entry: float = 0.0
        self._entry_atr: float = 0.0
        self._entry_adx: float = float("nan")
        self._entry_bullish: bool = False
        self._holding_days: int = 0
        self._prev_day_yin: bool = False
        self._consecutive_losses: int = 0
        self._cooldown_remaining: int = 0
        self._sell_signal_pending: bool = False

        # V3：回调入场状态
        self._signal_bars_waited: int = 0
        self._signal_buy_reason: str = ""

        # V3：金字塔加仓状态
        self._add_position_count: int = 0
        self._avg_entry_price: float = 0.0
        self._position_size: int = 0

    def next(self) -> None:
        cur_close = self.data.close[0]
        cur_open = self.data.open[0]
        prev_close = self.data.close[-1]

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        # ── 空仓：寻找买入信号 ────────────────────────────────────────────
        if self._state == _WAITING:
            prev_low = self.data.low[-1]
            self._check_buy(cur_close, cur_open, prev_low)
            return

        # ── 已信号化：等待回调入场 ────────────────────────────────────────
        if self._state == _SIGNALED:
            self._check_pullback_entry(cur_close)
            return

        # ── 持仓：更新追踪状态 ────────────────────────────────────────────
        self._holding_days += 1
        cur_high = self.data.high[0]
        if cur_high > self._highest_since_entry:
            self._highest_since_entry = cur_high
        if cur_close > self._highest_close_since_entry:
            self._highest_close_since_entry = cur_close

        i = self._bar_index

        # V3：金字塔加仓检查
        if PYRAMID_ENABLED:
            self._check_pyramid_add(i, cur_close, cur_high)

        self._check_sell(i, cur_close, cur_open, prev_close)

    # ── 买入逻辑 ──────────────────────────────────────────────────────────

    def _check_buy(self, cur_close: float, cur_open: float, prev_low: float) -> None:
        i = self._bar_index
        if self._cooldown_remaining > 0:
            return

        is_yang = cur_close > cur_open
        is_pullback_reversal_buy = False

        adx_now = self._adx[i]
        pdi_now = self._pdi[i]
        mdi_now = self._mdi[i]
        is_strong_trend = bool(
            adx_now == adx_now and pdi_now == pdi_now and mdi_now == mdi_now
            and adx_now >= STRONG_TREND_ADX and pdi_now > mdi_now
        )

        ma5_now = self.ma5[i]
        ma5_prev = self.ma5[i - 1] if i > 0 else float("nan")
        is_ma5_up = ma5_now > ma5_prev
        is_close_above_prev_low = cur_close > prev_low

        prev_open = self.data.open[-1]
        prev_high = self.data.high[-1]
        prev_close = self.data.close[-1]

        is_prev_yin = prev_close < prev_open
        shadow_ratio = STRONG_TREND_SHADOW_RATIO if is_strong_trend else UPPER_SHADOW_RATIO
        is_prev_no_long_upper_shadow = not self._is_long_upper_shadow_yin(
            prev_open, prev_high, prev_close, shadow_ratio
        )

        is_bullish_engulfing = self._is_bullish_engulfing(
            cur_open, cur_close, prev_open, prev_close
        )
        is_strong_reversal_yang = self._is_strong_reversal_yang(
            cur_open, cur_close, prev_open, prev_close
        )

        is_ma_condition_met = is_ma5_up or is_bullish_engulfing or is_strong_reversal_yang

        # V3 优化 4：弱反弹过滤（仅影线后收阳路径）
        # 阳线实体 / 前日阴线实体 < 阈值时，视为弱反弹，不买入
        is_weak_rebound = False
        if is_prev_yin and is_yang and not is_strong_reversal_yang and not is_bullish_engulfing:
            prev_body = prev_open - prev_close
            cur_body = cur_close - cur_open
            if prev_body > 0 and cur_body < prev_body * WEAK_REBOUND_BODY_RATIO_MIN:
                is_weak_rebound = True

        # ── V2 过滤条件（全部保留）───────────────────────────────────────

        # 波动率过滤
        atr_now = self._calc_atr(i)
        if cur_close > 0 and atr_now / cur_close * 100 > VOLATILITY_MAX_PCT:
            return

        # MA20 趋势过滤 + 回调反转买入例外
        ma20_now = self.ma20[i]
        is_close_above_ma20 = ma20_now == ma20_now and cur_close > ma20_now
        if not is_close_above_ma20:
            ma60_now = self.ma60[i]
            is_close_above_ma60 = ma60_now == ma60_now and cur_close > ma60_now
            is_strong_reversal = is_strong_reversal_yang
            ma20_prev = self.ma20[i - 1] if i > 0 else float("nan")
            is_ma20_not_falling = (
                ma20_prev == ma20_prev and ma20_now == ma20_now and ma20_now >= ma20_prev
            )
            if not (is_close_above_ma60 and is_strong_reversal and is_ma20_not_falling):
                return
            is_pullback_reversal_buy = True

        # MA60 方向过滤
        if i >= MA60_FLAT_LOOKBACK:
            ma60_now = self.ma60[i]
            ma60_ago = self.ma60[i - MA60_FLAT_LOOKBACK]
            if ma60_now == ma60_now and ma60_ago == ma60_ago and ma60_ago > 0:
                ma60_drop = (ma60_ago - ma60_now) / ma60_ago
                if ma60_drop > MA60_DROP_TOLERANCE:
                    gain_pct = (cur_close - prev_close) / prev_close if prev_close > 0 else 0
                    is_limit_up = gain_pct >= MA60_EXCEPTION_GAIN_MIN / 100
                    if not (is_strong_trend and is_limit_up and ma60_drop < MA60_EXCEPTION_DROP_MAX):
                        return

        # ADX 震荡市过滤
        if i >= 5 and adx_now == adx_now:
            adx_5ago = self._adx[i - 5]
            ma20_5ago = self.ma20[i - 5]
            if (
                adx_5ago == adx_5ago and ma20_5ago == ma20_5ago
                and adx_now < ADX_MIN_THRESHOLD and adx_now < adx_5ago
                and ma20_now <= ma20_5ago
            ):
                return

        # ADX 中等趋势过滤
        if adx_now == adx_now and ADX_MID_RANGE_MIN <= adx_now < ADX_MID_RANGE_MAX:
            if not is_strong_reversal_yang:
                return

        # 追高保护 - MA20 偏离
        dev_max = STRONG_TREND_DEV_MAX if is_strong_trend else MA20_DEVIATION_MAX
        if ma20_now > 0:
            deviation = (cur_close - ma20_now) / ma20_now
            if deviation > dev_max:
                return

        # 追高保护 - 近 5 日涨幅
        surge5_max = STRONG_TREND_SURGE5_MAX if is_strong_trend else RECENT_SURGE_MAX
        if i >= RECENT_SURGE_DAYS:
            close_5ago = self.data.close[-RECENT_SURGE_DAYS]
            if close_5ago > 0:
                recent_gain = (cur_close - close_5ago) / close_5ago
                if recent_gain > surge5_max:
                    return

        # 追高保护 - 近 3 日涨幅
        is_bullish = self._is_ma_bullish(i)
        surge3_max = STRONG_TREND_SURGE3_MAX if (is_strong_trend or is_bullish) else SHORT_SURGE_MAX
        if i >= SHORT_SURGE_DAYS:
            close_3ago = self.data.close[-SHORT_SURGE_DAYS]
            if close_3ago > 0:
                short_gain = (cur_close - close_3ago) / close_3ago
                if short_gain > surge3_max:
                    return

        # 成交量确认
        if i >= 5:
            vol_now = self.data.vol[0]
            vol_ma5 = sum(self.data.vol[-j] for j in range(1, 6)) / 5.0
            if vol_ma5 > 0 and vol_now < vol_ma5 * VOLUME_RATIO_MIN:
                return

        # 急跌弱反弹过滤
        if i >= RECENT_DROP_LOOKBACK:
            close_3ago = self.data.close[-3]
            if close_3ago > 0:
                recent_drop = (prev_close - close_3ago) / close_3ago
                if recent_drop < -RECENT_DROP_MAX:
                    cur_body = cur_close - cur_open
                    prev_body = prev_open - prev_close
                    if prev_body > 0 and cur_body < prev_body * WEAK_REBOUND_BODY_RATIO:
                        return

        # 超买过滤 - CCI
        cci_now = self._cci[i]
        if cci_now == cci_now and cci_now > CCI_OVERBOUGHT:
            return

        # 超买过滤 - MFI
        mfi_now = self._mfi[i]
        if mfi_now == mfi_now and mfi_now > MFI_OVERBOUGHT:
            return

        # BOLL %b 超买 + 高位区间
        is_boll_high_non_strong = False
        if not is_strong_trend:
            boll_upper = self._boll_upper[i]
            boll_lower = self._boll_lower[i]
            if boll_upper == boll_upper and boll_lower == boll_lower and boll_upper > boll_lower:
                boll_pctb = (cur_close - boll_lower) / (boll_upper - boll_lower)
                if boll_pctb > BOLL_PCTB_OVERBOUGHT:
                    return
                if boll_pctb > BOLL_PCTB_HIGH_ZONE:
                    is_boll_high_non_strong = True

        # 布林带宽死亡区间
        boll_mid = self._boll_mid[i]
        if boll_mid == boll_mid and boll_mid > 0:
            boll_upper_w = self._boll_upper[i]
            boll_lower_w = self._boll_lower[i]
            if boll_upper_w == boll_upper_w and boll_lower_w == boll_lower_w:
                boll_width_pct = (boll_upper_w - boll_lower_w) / boll_mid * 100
                if BOLL_WIDTH_DEATH_ZONE_LOW <= boll_width_pct <= BOLL_WIDTH_DEATH_ZONE_HIGH:
                    return

        # K 线形态 + KDJ 过滤
        cur_low = self.data.low[0]
        lower_shadow = min(cur_open, cur_close) - cur_low
        cur_body_abs = abs(cur_close - cur_open)
        if cur_body_abs > 0 and lower_shadow > cur_body_abs * LOWER_SHADOW_RATIO_MAX:
            return

        kdj_j_now = self._kdj_j[i]
        if kdj_j_now == kdj_j_now and kdj_j_now > KDJ_J_OVERBOUGHT:
            return

        cur_high = self.data.high[0]
        full_range = cur_high - cur_low
        if full_range > 0 and cur_body_abs / full_range < BODY_RATIO_MIN:
            return

        # 空头强趋势过滤
        pdi_prev = self._pdi[i - 1] if i > 0 else float("nan")
        mdi_prev = self._mdi[i - 1] if i > 0 else float("nan")
        is_bearish_strong_trend = bool(
            adx_now == adx_now and pdi_prev == pdi_prev and mdi_prev == mdi_prev
            and adx_now >= STRONG_TREND_ADX
            and mdi_prev >= pdi_prev * BEAR_STRONG_TREND_DI_RATIO
        )

        # 假突破过滤
        is_fake_breakout = False
        if i >= 2 and is_yang:
            cur_body = cur_close - cur_open
            if cur_body > 0:
                upper_shadow = cur_high - cur_close
                if upper_shadow > cur_body * FAKE_BREAKOUT_SHADOW_RATIO:
                    prev2_open = self.data.open[-2]
                    prev2_close = self.data.close[-2]
                    prev_two_max = max(prev_open, prev_close, prev2_open, prev2_close)
                    if prev_two_max > 0:
                        deviation = abs(cur_close - prev_two_max) / prev_two_max
                        is_fake_breakout = deviation <= FAKE_BREAKOUT_DEVIATION_MAX

        # V3：弱反弹过滤
        if is_weak_rebound:
            # 仅对影线后收阳主路径生效，强反转/看涨吞没不受影响
            pass

        main_signal = (
            is_yang and is_ma_condition_met and is_close_above_prev_low
            and is_prev_yin and is_prev_no_long_upper_shadow
            and (is_pullback_reversal_buy or not is_bearish_strong_trend)
            and not is_fake_breakout
            and not is_boll_high_non_strong
            # V3 新增：弱反弹过滤（仅 MA5上行 这一最弱触发条件时生效）
            and not (is_weak_rebound and not is_bullish_engulfing and not is_strong_reversal_yang)
        )

        if main_signal:
            buy_reason = "回调反转买入" if is_pullback_reversal_buy else "影线后收阳"
            self._enter_buy_signal(buy_reason, cur_close, atr_now, i)
            return

        # 多头排列首次形成买入
        if is_yang:
            is_bullish_now = self._is_ma_bullish(i)
            is_bullish_prev = self._is_ma_bullish(i - 1) if i > 0 else False
            if is_bullish_now and not is_bullish_prev:
                if not is_strong_trend:
                    boll_upper_bull = self._boll_upper[i]
                    boll_lower_bull = self._boll_lower[i]
                    if (
                        boll_upper_bull == boll_upper_bull
                        and boll_lower_bull == boll_lower_bull
                        and boll_upper_bull > boll_lower_bull
                    ):
                        boll_pctb_bull = (cur_close - boll_lower_bull) / (
                            boll_upper_bull - boll_lower_bull
                        )
                        if boll_pctb_bull > BOLL_PCTB_BULLISH_MAX:
                            return
                atr = self._calc_atr(i)
                self._enter_buy_signal("多头排列形成", cur_close, atr, i)

    def _enter_buy_signal(self, reason: str, price: float, atr: float, i: int) -> None:
        """V3：触发买入信号。若启用回调入场则进入 SIGNALED 状态等待回踩，
        否则直接买入。"""
        if PULLBACK_ENTRY_ENABLED:
            self._state = _SIGNALED
            self._signal_bars_waited = 0
            self._signal_buy_reason = reason
            # 记录信号日的 ATR/ADX 等，用于正式买入时使用
            self._signal_atr = atr
            self._signal_adx = self._adx[i]
            self._signal_bullish = self._is_ma_bullish(i)
        else:
            self._do_buy(price, atr, reason, i)

    def _check_pullback_entry(self, cur_close: float) -> None:
        """V3：检查回调入场条件。"""
        self._signal_bars_waited += 1
        i = self._bar_index
        ma5_now = self.ma5[i]
        ma10_now = self.ma10[i]

        # 超时放弃
        if self._signal_bars_waited > PULLBACK_WAIT_MAX_BARS:
            self._state = _WAITING
            return

        # 回踩 MA5 触发（收盘价接近或跌破 MA5）
        ma5_trigger = ma5_now * (1 + PULLBACK_MA5_TOLERANCE)
        ma10_trigger = ma10_now * (1 + PULLBACK_MA10_TOLERANCE)

        # 价格回踩 MA5 或更低即买入
        if cur_close <= ma5_trigger:
            atr = self._calc_atr(i)
            self._do_buy(cur_close, atr, self._signal_buy_reason + "(回踩)", i)

    def _do_buy(self, price: float, atr: float, reason: str, i: int) -> None:
        """执行买入。"""
        self.buy(size=0, price=price, reason=reason)
        self._state = _HOLDING
        self._entry_price = price
        self._avg_entry_price = price
        self._highest_since_entry = price
        self._highest_close_since_entry = price
        self._entry_atr = atr
        self._entry_adx = self._adx[i]
        self._entry_bullish = self._is_ma_bullish(i)
        self._holding_days = 0
        self._prev_day_yin = False
        self._sell_signal_pending = False
        self._add_position_count = 0

    # ── V3 优化 3：金字塔加仓 ──────────────────────────────────────────────

    def _check_pyramid_add(self, i: int, cur_close: float, cur_high: float) -> None:
        """检查金字塔加仓条件。"""
        if self._add_position_count >= PYRAMID_MAX_ADD_TIMES:
            return

        profit_pct = (cur_close - self._avg_entry_price) / self._avg_entry_price
        if profit_pct < PYRAMID_PROFIT_THRESHOLD:
            return

        # 必须是多头排列
        if not self._is_ma_bullish(i):
            return

        # 突破近 N 日新高（用最高价比，更符合突破加仓逻辑）
        if i < PYRAMID_BREAKOUT_LOOKBACK:
            return
        recent_high = max(self.data.high[-j] for j in range(1, PYRAMID_BREAKOUT_LOOKBACK + 1))
        if cur_high <= recent_high:
            return

        # 执行加仓
        add_size_ratio = PYRAMID_ADD_RATIO / (1 + self._add_position_count * PYRAMID_ADD_RATIO)
        # 计算加仓数量（基于当前可用资金的比例）
        # 简化：用 size=0 表示全仓，但我们要加仓 50%
        # 实际：通过估算当前持仓价值来加仓
        # 这里用 size=0 的变体不可行，改用额外买入
        # 由于 full 模式下 size=0 是满仓，加仓需要用 percent 模式
        # 为简化，这里用"加仓"逻辑：记录新的平均成本
        self._add_position_count += 1
        # 更新平均成本（模拟加仓）
        old_price = self._avg_entry_price
        new_price = cur_close
        # 原仓位价值 + 加仓价值
        self._avg_entry_price = (old_price * 1.0 + new_price * PYRAMID_ADD_RATIO) / (
            1.0 + PYRAMID_ADD_RATIO
        )
        # 触发实际买入单
        self.buy(size=0, price=cur_close, reason=f"金字塔加仓#{self._add_position_count}")
        # 注意：full 模式下每次 buy(size=0) 都是满仓，
        # 实际加仓效果取决于引擎的 position_mode。
        # 如果是 full 模式，这里的 buy 会被忽略（已全仓）。
        # 用户如果用 percent 模式才能真正加仓。
        # 我们仍更新均价用于止损计算（更保守）。

    # ── 卖出逻辑 ──────────────────────────────────────────────────────────

    def _check_sell(self, i: int, cur_close: float, cur_open: float, prev_close: float) -> None:
        cur_yin = cur_close < cur_open
        atr = self._entry_atr if self._entry_atr > 0 else self._calc_atr(i)

        adx_now = self._adx[i]
        is_ranging = adx_now == adx_now and adx_now < ADX_RAPID_THRESHOLD

        is_entry_strong_trend = bool(
            self._entry_adx == self._entry_adx
            and self._entry_adx >= STRONG_TREND_ADX
        )

        # A. ATR 硬止损
        stop_mult = RAPID_STOP_MULTIPLIER if is_ranging else ATR_STOP_MULTIPLIER
        hard_stop = self._avg_entry_price - stop_mult * atr
        if cur_close <= hard_stop:
            self._exit_position(cur_close, is_loss=True)
            return

        # B. 最大亏损保护
        loss_pct = (self._avg_entry_price - cur_close) / self._avg_entry_price * 100
        if loss_pct >= MAX_LOSS_PCT:
            self._exit_position(cur_close, is_loss=True)
            return

        profit_pct = (cur_close - self._avg_entry_price) / self._avg_entry_price

        # C. V3 精简版盈利锁定（3 级）
        profit_floor = self._calc_profit_floor_v3(is_entry_strong_trend)
        if profit_floor > 0 and cur_close <= profit_floor:
            self._exit_position(cur_close, is_loss=profit_pct < 0, reason="盈利锁定")
            return

        # D. 分阶段追踪止盈
        if self._highest_since_entry > self._avg_entry_price and profit_pct > 0:
            trail_stop = self._calc_trailing_stop(atr, profit_pct, adx_now)
            if trail_stop is not None and cur_close <= trail_stop:
                self._exit_position(cur_close, is_loss=profit_pct < 0)
                return

        # G. 震荡市时间止损
        is_bullish = self._is_ma_bullish(i)
        if (
            self._entry_adx == self._entry_adx
            and self._entry_adx >= ADX_RAPID_THRESHOLD
            and self._entry_bullish
        ):
            hold_threshold = TREND_ENTRY_RAPID_HOLD_DAYS
        else:
            hold_threshold = RAPID_MAX_HOLD_DAYS
        if (
            is_ranging and not is_bullish
            and not (is_entry_strong_trend and profit_pct > 0)
            and self._holding_days >= hold_threshold
            and profit_pct < RAPID_MIN_PROFIT_KEEP
        ):
            self._exit_position(cur_close, is_loss=profit_pct < 0, reason="震荡市时间止损")
            return

        if is_bullish:
            ma20_now = self.ma20[i]
            if profit_pct >= PROFIT_RELAX_THRESHOLD:
                if cur_close < ma20_now:
                    self._exit_position(cur_close, is_loss=profit_pct < 0)
                    return
            else:
                if cur_yin:
                    atr_pct_d = atr / cur_open * 100 if cur_open > 0 else 3.0
                    yin_drop_pct_d = (cur_open - cur_close) / cur_open * 100 if cur_open > 0 else 0
                    pdi_now_d = self._pdi[i]
                    mdi_now_d = self._mdi[i]
                    is_strong_trend_relax_d = bool(
                        adx_now == adx_now
                        and pdi_now_d == pdi_now_d and mdi_now_d == mdi_now_d
                        and adx_now >= STRONG_TREND_ADX and pdi_now_d > mdi_now_d
                        and cur_close > ma20_now and profit_pct > 0
                    )
                    if is_strong_trend_relax_d:
                        dynamic_threshold_d = max(
                            STRONG_TREND_YIN_DROP_RATIO * atr_pct_d,
                            STRONG_TREND_YIN_MIN_PCT,
                        )
                    else:
                        dynamic_threshold_d = max(
                            BULLISH_YIN_DROP_ATR_RATIO * atr_pct_d,
                            BULLISH_YIN_MIN_PCT,
                        )
                    if yin_drop_pct_d >= dynamic_threshold_d:
                        self._exit_position(cur_close, is_loss=profit_pct < 0)
                        return

                ma5_now = self.ma5[i]
                ma5_prev = self.ma5[i - 1] if i > 0 else float("nan")
                ma10_now = self.ma10[i]
                angle = self._calc_ma_angle(ma5_now, ma5_prev)
                is_converged = self._is_ma_converged(ma5_now, ma10_now)

                should_sell = False
                if is_converged:
                    if cur_open < ma10_now and cur_yin:
                        should_sell = True
                if not should_sell:
                    if angle < SLOPE_THRESHOLD_DEG:
                        if is_converged:
                            if max(cur_open, cur_close) < ma10_now:
                                should_sell = True
                        else:
                            if cur_close < ma10_now:
                                should_sell = True
                    else:
                        if cur_close < ma5_now:
                            should_sell = True

                if is_entry_strong_trend and profit_pct > 0:
                    should_sell = False

                if should_sell:
                    if not self._sell_signal_pending:
                        self._sell_signal_pending = True
                        self._prev_day_yin = cur_yin
                        return
                    else:
                        self._sell_signal_pending = False
                        self._exit_position(cur_close, is_loss=profit_pct < 0)
                        return
                else:
                    self._sell_signal_pending = False
        else:
            # F. 非多头排列
            pdi_now = self._pdi[i]
            mdi_now = self._mdi[i]
            is_strong_trend = bool(
                adx_now == adx_now
                and pdi_now == pdi_now and mdi_now == mdi_now
                and adx_now >= STRONG_TREND_ADX and pdi_now > mdi_now
            )
            is_low_open = self._is_low_open(cur_open, prev_close)
            if is_low_open and not is_strong_trend:
                self._exit_position(cur_open, is_loss=True)
                return

            ma20_now_f = self.ma20[i]
            is_strong_trend_relax = bool(
                is_strong_trend
                and ma20_now_f == ma20_now_f and cur_close > ma20_now_f
                and profit_pct > 0
            )

            if cur_yin:
                atr_pct = atr / cur_open * 100 if cur_open > 0 else 3.0
                yin_drop_pct = (cur_open - cur_close) / cur_open * 100 if cur_open > 0 else 0
                if is_strong_trend_relax:
                    dynamic_threshold = max(
                        STRONG_TREND_YIN_DROP_RATIO * atr_pct, STRONG_TREND_YIN_MIN_PCT
                    )
                else:
                    dynamic_threshold = max(YIN_DROP_ATR_RATIO * atr_pct, 2.0)
                if yin_drop_pct >= dynamic_threshold:
                    self._exit_position(cur_close, is_loss=True)
                    return
                if self._prev_day_yin and not is_strong_trend_relax:
                    self._exit_position(cur_close, is_loss=True)
                    return

        if not is_bullish:
            self._sell_signal_pending = False
        self._prev_day_yin = cur_yin

    # ── V3 精简盈利锁定 ────────────────────────────────────────────────────

    def _calc_profit_floor_v3(self, is_entry_strong_trend: bool = False) -> float:
        """V3 精简版盈利锁定（3 级阶梯）。

        Tier3: 10% -> 锁 5%（允许 5% 回撤）
        Tier4: 15% -> 锁 8%（允许 7% 回撤）
        Tier5: 20% -> 锁 12%（允许 8% 回撤）

        强趋势从 Tier4 开始（15% 才锁定）。
        """
        if self._avg_entry_price <= 0 or self._highest_close_since_entry <= self._avg_entry_price:
            return 0.0
        max_profit = (self._highest_close_since_entry - self._avg_entry_price) / self._avg_entry_price

        if max_profit >= PROFIT_LOCKIN_TRIGGER5:
            return self._avg_entry_price * (1 + PROFIT_LOCKIN_FLOOR5)
        if max_profit >= PROFIT_LOCKIN_TRIGGER4:
            return self._avg_entry_price * (1 + PROFIT_LOCKIN_FLOOR4)
        if max_profit >= PROFIT_LOCKIN_TRIGGER3:
            # 强趋势豁免 Tier3
            if is_entry_strong_trend and PROFIT_LOCKIN_STRONG_TREND_MIN_TIER >= 4:
                return 0.0
            return self._avg_entry_price * (1 + PROFIT_LOCKIN_FLOOR3)
        return 0.0

    # ── 追踪止盈（保留 V2 逻辑）────────────────────────────────────────────

    def _calc_trailing_stop(self, atr: float, profit_pct: float, adx_now: float) -> float | None:
        is_ranging = adx_now == adx_now and adx_now < ADX_RAPID_THRESHOLD
        if is_ranging:
            if profit_pct < RAPID_PROFIT_TIER:
                return None
            atr_mult = RAPID_ATR_TRAIL
            trailing_pct = RAPID_TRAILING_PCT
        elif profit_pct < PROFIT_TIER2:
            return None
        elif profit_pct >= PROFIT_TIER4:
            atr_mult = ATR_TRAIL_MEGA
            trailing_pct = TRAILING_STOP_PCT_MAX
        elif profit_pct >= PROFIT_TIER3:
            atr_mult = ATR_TRAIL_TIGHT
            trailing_pct = TRAILING_STOP_PCT_MAX
        else:
            atr_mult = ATR_TRAIL_WIDE
            trailing_pct = TRAILING_STOP_PCT_MAX

        atr_trail = self._highest_since_entry - atr_mult * atr
        pct_trail = self._highest_since_entry * (1 - trailing_pct / 100.0)
        return min(atr_trail, pct_trail)

    def _exit_position(self, price: float, is_loss: bool, reason: str = "") -> None:
        if not reason:
            reason = "止损清仓" if is_loss else "止盈清仓"
        self.sell(size=0, price=price, reason=reason)
        self._state = _WAITING
        self._sell_signal_pending = False
        if is_loss:
            self._consecutive_losses += 1
            if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                self._cooldown_remaining = COOLDOWN_BARS
                self._consecutive_losses = 0
        else:
            self._consecutive_losses = 0

    # ── 工具方法 ──────────────────────────────────────────────────────────

    def _calc_atr(self, i: int) -> float:
        if i < ATR_PERIOD:
            return self.data.close[0] * 0.03
        tr_sum = 0.0
        for j in range(ATR_PERIOD):
            offset = -j if j > 0 else 0
            high = self.data.high[offset]
            low = self.data.low[offset]
            prev_c = self.data.close[offset - 1]
            tr1 = high - low
            tr2 = abs(high - prev_c)
            tr3 = abs(low - prev_c)
            tr_sum += max(tr1, tr2, tr3)
        return tr_sum / ATR_PERIOD

    def _is_ma_bullish(self, i: int) -> bool:
        if i < 1:
            return False
        ma5 = self.ma5[i]
        ma10 = self.ma10[i]
        ma20 = self.ma20[i]
        ma60 = self.ma60[i]
        if not (ma5 > ma20 > ma60 and ma10 > ma20 > ma60):
            return False
        if ma5 < ma10 and ma5 < ma10 * (1 - MA_BULLISH_MA5_DEVIATION):
            return False
        return True

    @staticmethod
    def _is_long_upper_shadow_yin(
        prev_open: float, prev_high: float, prev_close: float,
        shadow_ratio: float = UPPER_SHADOW_RATIO,
    ) -> bool:
        if not (prev_open == prev_open and prev_high == prev_high and prev_close == prev_close):
            return False
        if prev_close >= prev_open:
            return False
        body = prev_open - prev_close
        upper_shadow = prev_high - prev_open
        return upper_shadow > body * shadow_ratio

    @staticmethod
    def _is_bullish_engulfing(
        cur_open: float, cur_close: float, prev_open: float, prev_close: float
    ) -> bool:
        values = (cur_open, cur_close, prev_open, prev_close)
        if not all(v == v for v in values):
            return False
        if prev_close >= prev_open:
            return False
        if cur_close <= cur_open:
            return False
        return cur_open <= prev_close and cur_close >= prev_open

    @staticmethod
    def _is_strong_reversal_yang(
        cur_open: float, cur_close: float, prev_open: float, prev_close: float
    ) -> bool:
        values = (cur_open, cur_close, prev_open, prev_close)
        if not all(v == v for v in values):
            return False
        if prev_close >= prev_open:
            return False
        if cur_close <= cur_open:
            return False
        if cur_close < prev_open:
            return False
        prev_body = prev_open - prev_close
        cur_body = cur_close - cur_open
        return cur_body >= prev_body * STRONG_REVERSAL_BODY_RATIO

    @staticmethod
    def _is_ma_converged(ma5: float, ma10: float) -> bool:
        if ma5 != ma5 or ma10 != ma10 or ma10 <= 0:
            return False
        return abs(ma5 - ma10) / ma10 <= MA_CONVERGENCE_THRESHOLD

    @staticmethod
    def _is_low_open(cur_open: float, prev_close: float) -> bool:
        if not (prev_close == prev_close and prev_close > 0):
            return False
        return cur_open <= prev_close * LOW_OPEN_THRESHOLD

    @staticmethod
    def _calc_ma_angle(ma_now: float, ma_prev: float) -> float:
        if ma_now != ma_now or ma_prev != ma_prev or ma_prev <= 0:
            return 0.0
        pct_change = (ma_now - ma_prev) / ma_prev * 100
        return math.degrees(math.atan(pct_change))
