"""影线后收阳线策略。

买入条件（全部满足）：
  1. 当天收阳线（close > open）
  2. MA5 上行 或 当日为标准反转形态
     - MA5 上行：MA5 日均线斜率大于 0（MA5 上行）
     - 标准反转形态（MA5 下行时作为替代买入信号）：
       * 刺透形态：当日开盘低于前日最低价，
         收盘高于前日实体中点但低于前日开盘价
       * 看涨吞没：当日阳线实体完全覆盖前日阴线实体
         （开盘 <= 前日收盘，收盘 >= 前日开盘）
       * 启明星：前两日大阴线 + 前一日小实体星线（在前两日实体之下）
         + 当日阳线收盘深入前两日实体
       * 强势反转阳线：当日阳线收盘突破前日实体顶部（收盘 >= 前日开盘），
         且实体 >= 前日阴线实体 × 2 倍（强势放量反转，允许轻微高开）
  3. 当天收盘价 > 前一天最低价（close > prev_low）
  4. 前一天必须收阴线（prev_close < prev_open）
  5. 前一天阴线不能有长上影线（上影 > 实体 × UPPER_SHADOW_RATIO 则排除）
  6. 非极端追高：前5日涨幅 <= RECENT_GAIN5_MAX(10%) 或 偏离MA20 <= DEV_MA20_MAX(15%)
     或 ADX <= ADX_MAX(50)
     （三个条件同时超过阈值视为极端追高，不买入。数据验证：8股681笔中
     极端追高且ADX>50的交易全部为亏损，ADX<=50的包含大盈利单如600550 +64.35%）

以当天收盘价买入。

卖出规则（持仓后逐日检查）：
  - 非均线多头排列：现有卖出逻辑（优先级从高到低）：
      * 低开 3 个点（开盘价 <= 前日收盘价 * 0.97）：以开盘价全部卖出
      * 收阴线（close < open）：以收盘价全部卖出
      * 买入后第一日收盘下跌（close < 前日收盘价）：以收盘价全部清仓
      * 其余情况：
          - 买入后第一日：以收盘价卖出一半
          - 后续交易日：持有，直到出现阴线或低开 3 个点时全部卖出
  - 均线多头排列（MA5 > MA20 > MA60、MA10 > MA20 > MA60 且四线斜率角度
    均 > -1°；MA5 若低于 MA10 偏离不超过 1%）：
    均线止损逻辑（优先级从高到低）：
      * 粘合止损：MA5/MA10 粘合（差值 <= 2%），开盘价跌破 10 日均线且当日收阴
      * 缓涨止损（非粘合）：MA5 斜率角度 < 45°，收盘价跌破 10 日均线
      * 缓涨止损（粘合）：MA5/MA10 粘合 且 MA5 斜率角度 < 45°，
        开盘价与收盘价中的最高价跌破 10 日均线
      * 急涨止损：MA5 斜率角度 >= 45°，收盘价跌破 5 日均线

.. note::

    本策略使用限价单（``price=`` 参数）在信号当根 K 线成交，实现"以收盘价
    买入"和"以开盘价/收盘价卖出"。部分卖出（卖一半）需要 ``position_mode="fixed"``，
    否则引擎默认 ``full`` 模式下所有卖出均为全仓。

用法（Python API）::

    from easy_tdx.backtest import BacktestEngine

    engine = BacktestEngine(
        strategy=ShadowYangStrategy,
        cash=100000,
        position_mode="fixed",   # 必须用 fixed 才能部分卖出
        warmup_bars=60,          # MA60 需 60 根 K 线预热
    )
    result = engine.run(df)

用法（CLI）::

    easy-tdx backtest SZ 000001 \\
        --strategy-file strategies/shadow_yang.py \\
        --position-mode fixed \\
        --table
"""

import math

import numpy as np
from numpy.typing import NDArray

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy

# ── 常量 ─────────────────────────────────────────────────────────────────────

LOW_OPEN_THRESHOLD = 0.97  # 低开 3 个点：open <= prev_close * 0.97
UPPER_SHADOW_RATIO = 1.5  # 长上影阈值：阴线上影 > 实体 × 此倍数则排除买入
STAR_BODY_RATIO = 0.5  # 启明星星线实体最大比例（占前两日实体）
STRONG_REVERSAL_BODY_RATIO = 2.0  # 强势反转阳线实体最小倍数（占前日阴线实体）
MA_LONG_PERIOD = 20  # 趋势判断均线周期（20 日均线）
MA_VERY_LONG_PERIOD = 60  # 多头排列长均线周期（60 日均线）
MA_STOP_PERIOD = 10  # 缓涨止损均线周期（10 日均线）
SLOPE_THRESHOLD_DEG = 45.0  # MA5 斜率角度阈值（度）
MA_BULLISH_SLOPE_MIN = -1.0  # 均线多头排列斜率最小角度（度）
MA_BULLISH_MA5_DEVIATION = 0.01  # MA5 低于 MA10 的最大允许偏离（1%）
MA_CONVERGENCE_THRESHOLD = 0.02  # MA5/MA10 粘合阈值（2%）
# 追高过滤：前5日涨幅 + 偏离MA20 双重确认（极端追高时不买入）
RECENT_GAIN5_MAX = 10.0  # 前5日涨幅阈值（%）：超过此值视为短期已大涨
DEV_MA20_MAX = 15.0  # 偏离MA20阈值（%）：超过此值视为价格远离中期均线
ADX_MAX = 50.0  # ADX阈值：追高过滤时ADX>此值才过滤（强趋势追高风险更高）

# 状态枚举（用字符串常量，避免引入 enum 开销）
_WAITING = "WAITING"  # 空仓等待买入信号
_HOLDING_FULL = "HOLDING_FULL"  # 刚买入，尚未卖出一半
_HOLDING_HALF = "HOLDING_HALF"  # 已卖出一半，持有剩余仓位


def _calc_adx(
    close: NDArray[np.float64],
    high: NDArray[np.float64],
    low: NDArray[np.float64],
) -> NDArray[np.float64]:
    """计算 ADX 指标（DMI 的趋势强度部分）。

    MyTT.DMI 返回 (+DI, -DI, ADX, ADXR)，此处只取 ADX 用于趋势强度判断。
    """
    _, _, adx, _ = MyTT.DMI(close, high, low)
    return adx


class ShadowYangStrategy(Strategy):
    """影线后收阳线策略。

    状态机::

        WAITING --买入--> HOLDING_FULL --卖一半/全卖--> HOLDING_HALF --全卖--> WAITING
                         HOLDING_FULL --全卖--------> WAITING
    """

    def init(self) -> None:
        self.ma5 = self.I(MyTT.MA, self.data.close, 5)
        self.ma10 = self.I(MyTT.MA, self.data.close, MA_STOP_PERIOD)
        self.ma20 = self.I(MyTT.MA, self.data.close, MA_LONG_PERIOD)
        self.ma60 = self.I(MyTT.MA, self.data.close, MA_VERY_LONG_PERIOD)
        self.adx = self.I(_calc_adx, self.data.close, self.data.high, self.data.low)
        self._state: str = _WAITING

    def next(self) -> None:
        cur_close = self.data.close[0]
        cur_open = self.data.open[0]
        prev_close = self.data.close[-1]

        # ── 空仓：寻找买入信号 ───────────────────────────────────────────────
        if self._state == _WAITING:
            prev_low = self.data.low[-1]
            self._check_buy(cur_close, cur_open, prev_low)
            return

        # ── 持仓：检查卖出条件 ───────────────────────────────────────────────
        i = self._bar_index
        is_bullish = self._is_ma_bullish(i)

        if is_bullish:
            # 均线多头排列：均线止损逻辑
            ma5_now = self.ma5[i]
            ma5_prev = self.ma5[i - 1] if i > 0 else float("nan")
            ma10_now = self.ma10[i]
            angle = self._calc_ma_angle(ma5_now, ma5_prev)

            should_sell = False

            is_converged = self._is_ma_converged(ma5_now, ma10_now)

            # 粘合止损：MA5/MA10 粘合（差值 <= 2%），开盘价跌破 MA10 且收阴
            if is_converged:
                if cur_open < ma10_now and cur_close < cur_open:
                    should_sell = True

            # 斜率止损（粘合未触发时检查）
            if not should_sell:
                if angle < SLOPE_THRESHOLD_DEG:
                    if is_converged:
                        # 粘合 + 缓涨：开盘价与收盘价中的最高价跌破 MA10 才止损
                        if max(cur_open, cur_close) < ma10_now:
                            should_sell = True
                    else:
                        # 非粘合 + 缓涨：收盘价跌破 10 日均线止损
                        if cur_close < ma10_now:
                            should_sell = True
                else:
                    # 急涨：收盘价跌破 5 日均线止损
                    if cur_close < ma5_now:
                        should_sell = True

            if should_sell:
                self.sell(size=0, price=cur_close)
                self._state = _WAITING
        else:
            # 非均线多头排列：现有卖出逻辑（优先级：低开 > 阴线 > 其余）
            is_low_open = self._is_low_open(cur_open, prev_close)
            is_yin = cur_close < cur_open

            if is_low_open:
                # 低开 3 个点：以开盘价全部卖出
                self.sell(size=0, price=cur_open)
                self._state = _WAITING
            elif is_yin:
                # 收阴线：以收盘价全部卖出
                self.sell(size=0, price=cur_close)
                self._state = _WAITING
            elif self._state == _HOLDING_FULL:
                if cur_close < prev_close:
                    # 买入后第一日收盘下跌：以收盘价全部清仓
                    self.sell(size=0, price=cur_close)
                    self._state = _WAITING
                else:
                    # 其余情况：以收盘价卖一半
                    self._sell_half(cur_close)
                    self._state = _HOLDING_HALF
            # HOLDING_HALF + 其余情况：持有，不做操作

    # ── 内部方法 ─────────────────────────────────────────────────────────────────

    def _check_buy(self, cur_close: float, cur_open: float, prev_low: float) -> None:
        """检查买入条件：阳线 + (MA5上行或标准反转形态) + 收盘>前日最低 + 前日收阴 + 前日无长上影。

        标准反转形态包括：刺透形态、看涨吞没、启明星、强势反转阳线。
        """
        is_yang = cur_close > cur_open

        # MA5 斜率：当前 MA5 > 前一根 MA5
        ma5_now = self.ma5[self._bar_index]
        ma5_prev = self.ma5[self._bar_index - 1] if self._bar_index > 0 else float("nan")
        # NaN 比较结果为 False，自然跳过预热期
        is_ma5_up = ma5_now > ma5_prev

        # 收盘价必须大于前日最低价（NaN 比较为 False，自然跳过首根 K 线）
        is_close_above_prev_low = cur_close > prev_low

        # 前一天 OHLC（用于判断阴线 + 上影线 + 刺透形态）
        prev_open = self.data.open[-1]
        prev_high = self.data.high[-1]
        prev_close = self.data.close[-1]

        # 前一天必须收阴线（close < open）
        is_prev_yin = prev_close < prev_open

        # 前一天阴线不能有长上影线（上影 > 实体 × UPPER_SHADOW_RATIO 则排除买入）
        is_prev_no_long_upper_shadow = not self._is_long_upper_shadow_yin(
            prev_open, prev_high, prev_close
        )

        # 刺透形态：MA5 下行时作为替代买入信号
        is_piercing_line = self._is_piercing_line(
            cur_open, cur_close, prev_open, prev_close, prev_low
        )

        # 看涨吞没形态：MA5 下行时作为替代买入信号
        is_bullish_engulfing = self._is_bullish_engulfing(
            cur_open, cur_close, prev_open, prev_close
        )

        # 启明星形态：MA5 下行时作为替代买入信号
        is_morning_star = self._is_morning_star(
            cur_open,
            cur_close,
            prev_open,
            prev_close,
            self.data.open[-2],
            self.data.close[-2],
        )

        # 强势反转阳线：MA5 下行时作为替代买入信号
        is_strong_reversal_yang = self._is_strong_reversal_yang(
            cur_open, cur_close, prev_open, prev_close
        )

        # MA 条件：MA5 上行 或 标准反转形态（刺透/吞没/启明星/强势反转）
        is_ma_condition_met = (
            is_ma5_up
            or is_piercing_line
            or is_bullish_engulfing
            or is_morning_star
            or is_strong_reversal_yang
        )

        # 组合追高过滤：前5日涨幅 + 偏离MA20 + ADX 三重确认
        # 数据验证（8股681笔）：极端追高（前5日涨幅>10% 且 vsMA20>15%）的交易中，
        # ADX>50 的全部为亏损，ADX<=50 的包含大盈利单（如 600550 +64.35%）
        i = self._bar_index
        if i >= 5:
            close_5ago = self.data.close[-5]
            recent_gain5 = (cur_close - close_5ago) / close_5ago * 100
            ma20_now = self.ma20[i]
            dev_ma20 = (cur_close - ma20_now) / ma20_now * 100
            adx_now = self.adx[i]
            is_extreme_chase = (
                recent_gain5 > RECENT_GAIN5_MAX and dev_ma20 > DEV_MA20_MAX and adx_now > ADX_MAX
            )
        else:
            is_extreme_chase = False

        if (
            is_yang
            and is_ma_condition_met
            and is_close_above_prev_low
            and is_prev_yin
            and is_prev_no_long_upper_shadow
            and not is_extreme_chase
        ):
            self.buy(size=0, price=cur_close)
            self._state = _HOLDING_FULL

    def _is_ma_bullish(self, i: int) -> bool:
        """判断是否为均线多头排列。

        条件：
          1. MA5 > MA20 > MA60 且 MA10 > MA20 > MA60（多头排列）；
             MA5 若低于 MA10，偏离不超过 MA_BULLISH_MA5_DEVIATION（1%）
          2. MA5/MA10/MA20/MA60 斜率角度均 > MA_BULLISH_SLOPE_MIN（均线上行）

        NaN 值时比较返回 False，自然跳过预热期。
        """
        if i < 1:
            return False

        ma5 = self.ma5[i]
        ma10 = self.ma10[i]
        ma20 = self.ma20[i]
        ma60 = self.ma60[i]

        # 多头排列：MA5 > MA20 > MA60 且 MA10 > MA20 > MA60
        if not (ma5 > ma20 > ma60 and ma10 > ma20 > ma60):
            return False

        # MA5 若低于 MA10，偏离不超过 1%
        if ma5 < ma10 and ma5 < ma10 * (1 - MA_BULLISH_MA5_DEVIATION):
            return False

        # 四条均线斜率角度均 > 阈值
        # for ma_now, ma_prev in (
        #     (self.ma5[i], self.ma5[i - 1]),
        #     (self.ma10[i], self.ma10[i - 1]),
        #     (self.ma20[i], self.ma20[i - 1]),
        #     (self.ma60[i], self.ma60[i - 1]),
        # ):
        #     if self._calc_ma_angle(ma_now, ma_prev) <= MA_BULLISH_SLOPE_MIN:
        #         return False

        return True

    @staticmethod
    def _is_long_upper_shadow_yin(prev_open: float, prev_high: float, prev_close: float) -> bool:
        """判断前一天阴线是否有长上影线（上影 > 实体 × UPPER_SHADOW_RATIO）。

        仅当 prev_close < prev_open（阴线）时判断；阳线返回 False（放行）。
        NaN 输入返回 False。
        """
        # NaN 检查（NaN != NaN 为 True）
        if not (prev_open == prev_open and prev_high == prev_high and prev_close == prev_close):
            return False
        # 仅阴线判断，阳线放行
        if prev_close >= prev_open:
            return False
        body = prev_open - prev_close
        upper_shadow = prev_high - prev_open
        return upper_shadow > body * UPPER_SHADOW_RATIO

    @staticmethod
    def _is_piercing_line(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
        prev_low: float,
    ) -> bool:
        """判断当日是否为刺透形态（Piercing Line）。

        条件（全部满足）：
          1. 前一日阴线（prev_close < prev_open）
          2. 当日开盘低于前日最低价（cur_open < prev_low）
          3. 当日收盘高于前日实体中点（cur_close > (prev_open + prev_close) / 2）
          4. 当日收盘低于前日开盘价（cur_close < prev_open，区别于吞没形态）

        NaN 输入返回 False。
        """
        values = (cur_open, cur_close, prev_open, prev_close, prev_low)
        if not all(v == v for v in values):
            return False
        if prev_close >= prev_open:
            return False
        if cur_open >= prev_low:
            return False
        midpoint = (prev_open + prev_close) / 2
        if cur_close <= midpoint:
            return False
        return cur_close < prev_open

    @staticmethod
    def _is_bullish_engulfing(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
    ) -> bool:
        """判断当日是否为看涨吞没形态（Bullish Engulfing）。

        条件（全部满足）：
          1. 前一日阴线（prev_close < prev_open）
          2. 当日阳线（cur_close > cur_open）
          3. 当日开盘价 <= 前日收盘价（cur_open <= prev_close）
          4. 当日收盘价 >= 前日开盘价（cur_close >= prev_open）

        即当日阳线实体完全覆盖前日阴线实体。
        NaN 输入返回 False。
        """
        values = (cur_open, cur_close, prev_open, prev_close)
        if not all(v == v for v in values):
            return False
        if prev_close >= prev_open:
            return False
        if cur_close <= cur_open:
            return False
        return cur_open <= prev_close and cur_close >= prev_open

    @staticmethod
    def _is_morning_star(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
        prev2_open: float,
        prev2_close: float,
    ) -> bool:
        """判断当日是否为启明星形态（Morning Star）。

        三根 K 线形态：
          1. 前两日阴线（prev2_close < prev2_open），实体较大
          2. 前一日小实体星线（|prev_open - prev_close| < 前两日实体 × STAR_BODY_RATIO），
             星线实体在前两日实体之下（max(prev_open, prev_close) < prev2_close）
          3. 当日阳线（cur_close > cur_open），收盘价深入前两日实体
             （cur_close > 前两日实体中点）

        NaN 输入返回 False。
        """
        values = (cur_open, cur_close, prev_open, prev_close, prev2_open, prev2_close)
        if not all(v == v for v in values):
            return False
        # 前两日阴线
        if prev2_close >= prev2_open:
            return False
        # 前两日实体
        prev2_body = prev2_open - prev2_close
        # 前一日小实体星线
        prev_body = abs(prev_open - prev_close)
        if prev_body >= prev2_body * STAR_BODY_RATIO:
            return False
        # 星线实体在前两日实体之下
        if max(prev_open, prev_close) >= prev2_close:
            return False
        # 当日阳线
        if cur_close <= cur_open:
            return False
        # 当日收盘深入前两日实体
        midpoint = (prev2_open + prev2_close) / 2
        return cur_close > midpoint

    @staticmethod
    def _is_strong_reversal_yang(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
    ) -> bool:
        """判断当日是否为强势反转阳线（Strong Reversal Yang）。

        条件（全部满足）：
          1. 前一日阴线（prev_close < prev_open）
          2. 当日阳线（cur_close > cur_open）
          3. 当日收盘价 >= 前日开盘价（cur_close >= prev_open，收盘突破前日实体顶部）
          4. 当日实体 >= 前日实体 × STRONG_REVERSAL_BODY_RATIO（实体至少放大2倍）

        与看涨吞没的区别：不要求开盘 <= 前日收盘价（允许轻微高开），
        但要求实体明显放大（>= 2倍），确保是强势反转而非弱反弹。

        NaN 输入返回 False。
        """
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
        """判断 MA5 和 MA10 是否粘合（差值百分比 <= MA_CONVERGENCE_THRESHOLD）。

        以 MA10 为基准计算差值百分比：|MA5 - MA10| / MA10。
        NaN 或除零返回 False。
        """
        if ma5 != ma5 or ma10 != ma10 or ma10 <= 0:
            return False
        return abs(ma5 - ma10) / ma10 <= MA_CONVERGENCE_THRESHOLD

    def _sell_half(self, cur_close: float) -> None:
        """以收盘价卖出当前持仓的一半（向下取整到 100 股整手）。"""
        position_size = self.position["size"]
        half = int(position_size / 2 / 100) * 100
        if half > 0:
            self.sell(size=half, price=cur_close)

    @staticmethod
    def _is_low_open(cur_open: float, prev_close: float) -> bool:
        """判断是否低开 3 个点。

        Args:
            cur_open: 当日开盘价
            prev_close: 前日收盘价

        Returns:
            True 如果 open <= prev_close * 0.97（低开 3 个点）
        """
        # prev_close == prev_close 用于 NaN 检查（NaN != NaN 为 True）
        if not (prev_close == prev_close and prev_close > 0):
            return False
        return cur_open <= prev_close * LOW_OPEN_THRESHOLD

    @staticmethod
    def _calc_ma_angle(ma_now: float, ma_prev: float) -> float:
        """计算均线斜率角度（TDX 公式）。

        angle = atan((ma_now - ma_prev) / ma_prev * 100) * 180 / π

        1% 日变化率对应 45°。NaN 或除零返回 0.0（视为平缓）。
        """
        if ma_now != ma_now or ma_prev != ma_prev or ma_prev <= 0:
            return 0.0
        pct_change = (ma_now - ma_prev) / ma_prev * 100
        return math.degrees(math.atan(pct_change))
