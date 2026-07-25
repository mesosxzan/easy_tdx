"""影线后收阳线策略。

买入条件（四条件全部满足）：
  1. 当天收阳线（close > open）
  2. MA5 日均线斜率大于 0（MA5 上行）
  3. 当天收盘价 > 前一天最低价（close > prev_low）
  4. 前一天阴线不能有长上影线（上影 > 实体 × UPPER_SHADOW_RATIO 则排除）

以当天收盘价买入。

卖出规则（持仓后逐日检查）：
  - 非均线多头排列：现有卖出逻辑（优先级从高到低）：
      * 低开 2 个点（开盘价 <= 前日收盘价 * 0.98）：以开盘价全部卖出
      * 收阴线（close < open）：以收盘价全部卖出
      * 买入后第一日收盘下跌（close < 前日收盘价）：以收盘价全部清仓
      * 其余情况：
          - 买入后第一日：以收盘价卖出一半
          - 后续交易日：持有，直到出现阴线或低开 2 个点时全部卖出
  - 均线多头排列（MA5 > MA20 > MA60、MA10 > MA20 > MA60 且四线斜率角度
    均 > 5°；MA5 若低于 MA10 偏离不超过 1%）：
    均线止损逻辑：
      * MA5 斜率角度 < 45°（缓涨）：收盘价跌破 10 日均线止损
      * MA5 斜率角度 >= 45°（急涨）：收盘价跌破 5 日均线止损

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

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy

# ── 常量 ─────────────────────────────────────────────────────────────────────

LOW_OPEN_THRESHOLD = 0.97  # 低开 2 个点：open <= prev_close * 0.98
UPPER_SHADOW_RATIO = 1.0  # 长上影阈值：阴线上影 > 实体 × 此倍数则排除买入
MA_LONG_PERIOD = 20  # 趋势判断均线周期（20 日均线）
MA_VERY_LONG_PERIOD = 60  # 多头排列长均线周期（60 日均线）
MA_STOP_PERIOD = 10  # 缓涨止损均线周期（10 日均线）
SLOPE_THRESHOLD_DEG = 45.0  # MA5 斜率角度阈值（度）
MA_BULLISH_SLOPE_MIN = -1.0  # 均线多头排列斜率最小角度（度）
MA_BULLISH_MA5_DEVIATION = 0.01  # MA5 低于 MA10 的最大允许偏离（1%）

# 状态枚举（用字符串常量，避免引入 enum 开销）
_WAITING = "WAITING"              # 空仓等待买入信号
_HOLDING_FULL = "HOLDING_FULL"    # 刚买入，尚未卖出一半
_HOLDING_HALF = "HOLDING_HALF"    # 已卖出一半，持有剩余仓位


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
            angle = self._calc_ma_angle(ma5_now, ma5_prev)

            if angle < SLOPE_THRESHOLD_DEG:
                # MA5 斜率 < 45°（缓涨）：收盘价跌破 10 日均线止损
                stop_ma = self.ma10[i]
            else:
                # MA5 斜率 >= 45°（急涨）：收盘价跌破 5 日均线止损
                stop_ma = self.ma5[i]

            if cur_close < stop_ma:
                self.sell(size=0, price=cur_close)
                self._state = _WAITING
        else:
            # 非均线多头排列：现有卖出逻辑（优先级：低开 > 阴线 > 其余）
            is_low_open = self._is_low_open(cur_open, prev_close)
            is_yin = cur_close < cur_open

            if is_low_open:
                # 低开 2 个点：以开盘价全部卖出
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

    def _check_buy(
        self, cur_close: float, cur_open: float, prev_low: float
    ) -> None:
        """检查买入条件：阳线 + MA5 斜率上行 + 收盘价 > 前日最低价 + 前日阴线无长上影。"""
        is_yang = cur_close > cur_open

        # MA5 斜率：当前 MA5 > 前一根 MA5
        ma5_now = self.ma5[self._bar_index]
        ma5_prev = self.ma5[self._bar_index - 1] if self._bar_index > 0 else float("nan")
        # NaN 比较结果为 False，自然跳过预热期
        is_ma5_up = ma5_now > ma5_prev

        # 收盘价必须大于前日最低价（NaN 比较为 False，自然跳过首根 K 线）
        is_close_above_prev_low = cur_close > prev_low

        # 前一天阴线不能有长上影线（上影 > 实体 × UPPER_SHADOW_RATIO 则排除买入）
        prev_open = self.data.open[-1]
        prev_high = self.data.high[-1]
        prev_close = self.data.close[-1]
        is_prev_no_long_upper_shadow = not self._is_long_upper_shadow_yin(
            prev_open, prev_high, prev_close
        )

        if (
            is_yang
            and is_ma5_up
            and is_close_above_prev_low
            and is_prev_no_long_upper_shadow
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
        for ma_now, ma_prev in (
            (self.ma5[i], self.ma5[i - 1]),
            (self.ma10[i], self.ma10[i - 1]),
            (self.ma20[i], self.ma20[i - 1]),
            (self.ma60[i], self.ma60[i - 1]),
        ):
            if self._calc_ma_angle(ma_now, ma_prev) <= MA_BULLISH_SLOPE_MIN:
                return False

        return True

    @staticmethod
    def _is_long_upper_shadow_yin(
        prev_open: float, prev_high: float, prev_close: float
    ) -> bool:
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

    def _sell_half(self, cur_close: float) -> None:
        """以收盘价卖出当前持仓的一半（向下取整到 100 股整手）。"""
        position_size = self.position["size"]
        half = int(position_size / 2 / 100) * 100
        if half > 0:
            self.sell(size=half, price=cur_close)

    @staticmethod
    def _is_low_open(cur_open: float, prev_close: float) -> bool:
        """判断是否低开 2 个点。

        Args:
            cur_open: 当日开盘价
            prev_close: 前日收盘价

        Returns:
            True 如果 open <= prev_close * 0.98（低开 2 个点）
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
