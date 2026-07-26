"""内置策略集合。

每个策略通过 :func:`~easy_tdx.backtest.strategies.registry.register_strategy`
登记到全局注册表，并声明参数 schema 供 Web API 表单动态渲染。

导入本模块即触发所有策略的注册。Web API / CLI 通过 ``get_registry()```
发现策略，无需手动枚举。
"""

from __future__ import annotations

import math

from easy_tdx.backtest.strategies.registry import (
    Param,
    ParametrizedStrategy,
    register_strategy,
)
from easy_tdx.MyTT import (
    ATR,
    BBI,
    BIAS,
    BOLL,
    CCI,
    CROSS,
    DMI,
    DPO,
    EMA,
    EMV,
    FSL,
    KDJ,
    KTN,
    MA,
    MACD,
    RSI,
    TAQ,
    TRIX,
    WR,
)

__all__: list[str] = []  # 注册副作用即可，无需导出符号


# ── 双均线交叉 ─────────────────────────────────────────────────────────────────


@register_strategy(
    name="ma_cross",
    label="双均线交叉",
    description="快线上穿慢线买入，快线下穿慢线卖出。最经典的趋势跟随策略。",
)
class MaCrossStrategy(ParametrizedStrategy):
    """快慢均线金叉买入、死叉卖出。"""

    params = [
        Param("fast", int, default=5, min_value=1, max_value=60, label="快线周期"),
        Param("slow", int, default=20, min_value=5, max_value=250, label="慢线周期"),
    ]

    def init(self) -> None:
        self.ma_fast = self.I(MA, self.data.close, self.p["fast"])
        self.ma_slow = self.I(MA, self.data.close, self.p["slow"])
        self.gold = self.I(CROSS, self.ma_fast, self.ma_slow)
        self.dead = self.I(CROSS, self.ma_slow, self.ma_fast)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── MACD 金叉 ──────────────────────────────────────────────────────────────────


@register_strategy(
    name="macd",
    label="MACD 金叉",
    description="DIF 上穿 DEA 买入（金叉），DIF 下穿 DEA 卖出（死叉）。",
)
class MacdStrategy(ParametrizedStrategy):
    """MACD 金叉/死叉。"""

    params = [
        Param("short", int, default=12, min_value=2, max_value=50, label="短期EMA"),
        Param("long", int, default=26, min_value=5, max_value=100, label="长期EMA"),
        Param("signal", int, default=9, min_value=2, max_value=50, label="信号周期"),
    ]

    def init(self) -> None:
        self.dif, self.dea, self._hist = self.I(
            MACD,
            self.data.close,
            self.p["short"],
            self.p["long"],
            self.p["signal"],
        )
        self.gold = self.I(CROSS, self.dif, self.dea)
        self.dead = self.I(CROSS, self.dea, self.dif)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── 布林带突破 ─────────────────────────────────────────────────────────────────


@register_strategy(
    name="boll_breakout",
    label="布林带突破",
    description="收盘价突破下轨买入，突破上轨卖出（均值回归思路）。",
)
class BollBreakoutStrategy(ParametrizedStrategy):
    """价格触及下轨买入、触及上轨卖出。"""

    params = [
        Param("n", int, default=20, min_value=5, max_value=100, label="周期"),
        Param("p", float, default=2.0, min_value=0.5, max_value=4.0, label="标准差倍数"),
    ]

    def init(self) -> None:
        self.upper, self.mid, self.lower = self.I(BOLL, self.data.close, self.p["n"], self.p["p"])

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        # 触及下轨买入（均值回归）；触及上轨获利了结
        if close <= self.lower[i] and self.position["size"] == 0:
            self.buy()
        elif close >= self.upper[i] and self.position["size"] > 0:
            self.sell()


# ── RSI 超买超卖 ───────────────────────────────────────────────────────────────


@register_strategy(
    name="rsi_reversal",
    label="RSI 超卖反弹",
    description="RSI 低于超卖线买入，RSI 高于超买线卖出。",
)
class RsiReversalStrategy(ParametrizedStrategy):
    """RSI 超卖买入、超买卖出。"""

    params = [
        Param("n", int, default=14, min_value=2, max_value=50, label="RSI周期"),
        Param("oversold", int, default=30, min_value=5, max_value=45, label="超卖线"),
        Param("overbought", int, default=70, min_value=55, max_value=95, label="超买线"),
    ]

    def init(self) -> None:
        self.rsi = self.I(RSI, self.data.close, self.p["n"])

    def next(self) -> None:
        i = self._bar_index
        rsi = self.rsi[i]
        if rsi <= self.p["oversold"] and self.position["size"] == 0:
            self.buy()
        elif rsi >= self.p["overbought"] and self.position["size"] > 0:
            self.sell()


# ── KDJ 金叉 ───────────────────────────────────────────────────────────────────


@register_strategy(
    name="kdj_cross",
    label="KDJ 金叉",
    description="K 线上穿 D 线买入（金叉），K 线下穿 D 线卖出（死叉）。",
)
class KdjCrossStrategy(ParametrizedStrategy):
    """KDJ K/D 金叉死叉。"""

    params = [
        Param("n", int, default=9, min_value=2, max_value=30, label="RSV周期"),
    ]

    def init(self) -> None:
        self.k, self.d, self._j = self.I(
            KDJ,
            self.data.close,
            self.data.high,
            self.data.low,
            self.p["n"],
        )
        self.gold = self.I(CROSS, self.k, self.d)
        self.dead = self.I(CROSS, self.d, self.k)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── EMA 双线交叉 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="ema_cross",
    label="EMA 双线交叉",
    description="指数均线金叉买入、死叉卖出。比简单均线反应更灵敏。",
)
class EmaCrossStrategy(ParametrizedStrategy):
    params = [
        Param("fast", int, default=12, min_value=2, max_value=60, label="快线周期"),
        Param("slow", int, default=26, min_value=5, max_value=120, label="慢线周期"),
    ]

    def init(self) -> None:
        self.ema_fast = self.I(EMA, self.data.close, self.p["fast"])
        self.ema_slow = self.I(EMA, self.data.close, self.p["slow"])
        self.gold = self.I(CROSS, self.ema_fast, self.ema_slow)
        self.dead = self.I(CROSS, self.ema_slow, self.ema_fast)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── 三均线系统 ────────────────────────────────────────────────────────────────


@register_strategy(
    name="triple_ma",
    label="三均线系统",
    description="短中长期均线多头排列买入、空头排列卖出。",
)
class TripleMaStrategy(ParametrizedStrategy):
    params = [
        Param("short", int, default=5, min_value=1, max_value=30, label="短期"),
        Param("mid", int, default=20, min_value=5, max_value=60, label="中期"),
        Param("long", int, default=60, min_value=20, max_value=250, label="长期"),
    ]

    def init(self) -> None:
        self.ma_s = self.I(MA, self.data.close, self.p["short"])
        self.ma_m = self.I(MA, self.data.close, self.p["mid"])
        self.ma_l = self.I(MA, self.data.close, self.p["long"])

    def next(self) -> None:
        i = self._bar_index
        if self.ma_s[i] > self.ma_m[i] > self.ma_l[i] and self.position["size"] == 0:
            self.buy()
        elif self.ma_s[i] < self.ma_m[i] < self.ma_l[i] and self.position["size"] > 0:
            self.sell()


# ── 唐安奇通道（海龟）────────────────────────────────────────────────────────


@register_strategy(
    name="donchian",
    label="唐安奇通道突破",
    description="突破N日最高价买入，跌破N日最低价卖出。海龟交易法核心。",
)
class DonchianStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=20, min_value=5, max_value=100, label="通道周期"),
    ]

    def init(self) -> None:
        self.upper, self._mid, self.lower = self.I(TAQ, self.data.high, self.data.low, self.p["n"])

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        if close >= self.upper[i] and self.position["size"] == 0:
            self.buy()
        elif close <= self.lower[i] and self.position["size"] > 0:
            self.sell()


# ── 肯特纳通道 ────────────────────────────────────────────────────────────────


@register_strategy(
    name="keltner",
    label="肯特纳通道",
    description="收盘价突破上轨买入，跌破下轨卖出。ATR-based 通道。",
)
class KeltnerStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=20, min_value=5, max_value=100, label="均线周期"),
        Param("m", int, default=10, min_value=2, max_value=50, label="ATR周期"),
    ]

    def init(self) -> None:
        self.upper, self._mid, self.lower = self.I(
            KTN, self.data.close, self.data.high, self.data.low, self.p["n"], self.p["m"]
        )

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        if close >= self.upper[i] and self.position["size"] == 0:
            self.buy()
        elif close <= self.lower[i] and self.position["size"] > 0:
            self.sell()


# ── BBI 多空指标 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="bbi",
    label="BBI 多空指标",
    description="收盘价上穿BBI买入，下穿BBI卖出。多空综合指标。",
)
class BbiStrategy(ParametrizedStrategy):
    params = [
        Param("m1", int, default=3, min_value=1, max_value=20, label="均线1"),
        Param("m2", int, default=6, min_value=2, max_value=30, label="均线2"),
        Param("m3", int, default=12, min_value=5, max_value=60, label="均线3"),
        Param("m4", int, default=20, min_value=10, max_value=120, label="均线4"),
    ]

    def init(self) -> None:
        self.bbi = self.I(
            BBI, self.data.close, self.p["m1"], self.p["m2"], self.p["m3"], self.p["m4"]
        )

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        if close > self.bbi[i] and self.position["size"] == 0:
            self.buy()
        elif close < self.bbi[i] and self.position["size"] > 0:
            self.sell()


# ── CCI 顺势指标 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="cci",
    label="CCI 超卖反弹",
    description="CCI 跌破-100后回升买入，涨破+100卖出。",
)
class CciStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=14, min_value=2, max_value=50, label="CCI周期"),
        Param("oversold", int, default=-100, min_value=-200, max_value=0, label="超卖线"),
        Param("overbought", int, default=100, min_value=0, max_value=200, label="超买线"),
    ]

    def init(self) -> None:
        self.cci = self.I(CCI, self.data.close, self.data.high, self.data.low, self.p["n"])

    def next(self) -> None:
        i = self._bar_index
        cci = self.cci[i]
        if cci <= self.p["oversold"] and self.position["size"] == 0:
            self.buy()
        elif cci >= self.p["overbought"] and self.position["size"] > 0:
            self.sell()


# ── WR 威廉指标 ───────────────────────────────────────────────────────────────


@register_strategy(
    name="wr_reversal",
    label="WR 威廉超卖",
    description="WR 进入超卖区（<-80）买入，进入超买区（>-20）卖出。",
)
class WrReversalStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=14, min_value=2, max_value=50, label="WR周期"),
        Param("oversold", int, default=-80, min_value=-100, max_value=-40, label="超卖线"),
        Param("overbought", int, default=-20, min_value=-60, max_value=0, label="超买线"),
    ]

    def init(self) -> None:
        self.wr, self._wr1 = self.I(WR, self.data.close, self.data.high, self.data.low, self.p["n"])

    def next(self) -> None:
        i = self._bar_index
        wr = self.wr[i]
        if wr <= self.p["oversold"] and self.position["size"] == 0:
            self.buy()
        elif wr >= self.p["overbought"] and self.position["size"] > 0:
            self.sell()


# ── BIAS 乖离率 ───────────────────────────────────────────────────────────────


@register_strategy(
    name="bias_reversal",
    label="BIAS 乖离反弹",
    description="乖离率低于负阈值（超跌）买入，高于正阈值（超涨）卖出。",
)
class BiasReversalStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=6, min_value=2, max_value=30, label="均线周期"),
        Param("threshold", float, default=5.0, min_value=1.0, max_value=20.0, label="乖离阈值%"),
    ]

    def init(self) -> None:
        self.bias, self._b2, self._b3 = self.I(BIAS, self.data.close, self.p["n"], 12, 24)

    def next(self) -> None:
        i = self._bar_index
        bias_pct = self.bias[i] * 100
        threshold = self.p["threshold"]
        if bias_pct <= -threshold and self.position["size"] == 0:
            self.buy()
        elif bias_pct >= threshold and self.position["size"] > 0:
            self.sell()


# ── DMI 趋向指标 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="dmi",
    label="DMI 趋向指标",
    description="+DI 上穿-DI 买入（多头趋强），+DI 下穿-DI 卖出。",
)
class DmiStrategy(ParametrizedStrategy):
    params = [
        Param("m1", int, default=14, min_value=2, max_value=30, label="DI周期"),
        Param("m2", int, default=6, min_value=2, max_value=20, label="ADX周期"),
    ]

    def init(self) -> None:
        self.pdi, self.mdi, self._adx, self._adxr = self.I(
            DMI, self.data.close, self.data.high, self.data.low, self.p["m1"], self.p["m2"]
        )
        self.gold = self.I(CROSS, self.pdi, self.mdi)
        self.dead = self.I(CROSS, self.mdi, self.pdi)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── TRIX 三重平滑 ─────────────────────────────────────────────────────────────


@register_strategy(
    name="trix",
    label="TRIX 三重平滑",
    description="TRIX 上穿信号线买入，下穿卖出。过滤短期波动的趋势指标。",
)
class TrixStrategy(ParametrizedStrategy):
    params = [
        Param("m1", int, default=12, min_value=2, max_value=30, label="TRIX周期"),
        Param("m2", int, default=20, min_value=5, max_value=60, label="信号周期"),
    ]

    def init(self) -> None:
        self.trix, self.trma = self.I(TRIX, self.data.close, self.p["m1"], self.p["m2"])
        self.gold = self.I(CROSS, self.trix, self.trma)
        self.dead = self.I(CROSS, self.trma, self.trix)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── EMV 简易波动 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="emv",
    label="EMV 简易波动",
    description="EMV 上穿0轴买入，下穿0轴卖出。量价结合指标。",
)
class EmvStrategy(ParametrizedStrategy):
    params = [
        Param("n", int, default=14, min_value=2, max_value=30, label="EMV周期"),
    ]

    def init(self) -> None:
        self.emv, self._maemv = self.I(
            EMV, self.data.high, self.data.low, self.data.vol, self.p["n"]
        )

    def next(self) -> None:
        i = self._bar_index
        if self.emv[i] > 0 and self.position["size"] == 0:
            self.buy()
        elif self.emv[i] < 0 and self.position["size"] > 0:
            self.sell()


# ── DPO 区间震荡 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="dpo",
    label="DPO 区间震荡",
    description="DPO 上穿信号线买入，下穿卖出。去除趋势的震荡指标。",
)
class DpoStrategy(ParametrizedStrategy):
    params = [
        Param("m1", int, default=20, min_value=5, max_value=60, label="DPO周期"),
    ]

    def init(self) -> None:
        self.dpo, self.madpo = self.I(DPO, self.data.close, self.p["m1"])
        self.gold = self.I(CROSS, self.dpo, self.madpo)
        self.dead = self.I(CROSS, self.madpo, self.dpo)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── ATR 通道突破 ──────────────────────────────────────────────────────────────


@register_strategy(
    name="atr_breakout",
    label="ATR 通道突破",
    description="收盘价突破 均线+K×ATR 买入，跌破 均线-K×ATR 卖出。",
)
class AtrBreakoutStrategy(ParametrizedStrategy):
    params = [
        Param("n_ma", int, default=20, min_value=5, max_value=100, label="均线周期"),
        Param("n_atr", int, default=20, min_value=5, max_value=50, label="ATR周期"),
        Param("k", float, default=2.0, min_value=0.5, max_value=5.0, label="ATR倍数"),
    ]

    def init(self) -> None:
        self.ma = self.I(MA, self.data.close, self.p["n_ma"])
        self.atr = self.I(ATR, self.data.close, self.data.high, self.data.low, self.p["n_atr"])

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        upper = self.ma[i] + self.p["k"] * self.atr[i]
        lower = self.ma[i] - self.p["k"] * self.atr[i]
        if close >= upper and self.position["size"] == 0:
            self.buy()
        elif close <= lower and self.position["size"] > 0:
            self.sell()


# ── FSL 分水岭指标 ────────────────────────────────────────────────────────────


@register_strategy(
    name="fsl",
    label="FSL 分水岭",
    description="SWL 上穿 SWS 买入（多头占优），SWL 下穿 SWS 卖出（空头占优）。",
)
class FslStrategy(ParametrizedStrategy):
    """FSL 分水岭 SWL/SWS 金叉死叉。"""

    params = [
        Param(
            "capital",
            float,
            default=1e8,
            min_value=1e6,
            max_value=1e12,
            label="流通股本(股)",
        ),
    ]

    def init(self) -> None:
        self.swl, self.sws = self.I(FSL, self.data.close, self.data.vol, self.p["capital"])
        self.gold = self.I(CROSS, self.swl, self.sws)
        self.dead = self.I(CROSS, self.sws, self.swl)

    def next(self) -> None:
        i = self._bar_index
        if self.gold[i]:
            self.buy()
        elif self.dead[i] and self.position["size"] > 0:
            self.sell()


# ── 影线后收阳线 ─────────────────────────────────────────────────────────────


@register_strategy(
    name="shadow_yang",
    label="影线后收阳线",
    description="阳线+(MA上行或刺透/吞没/启明星)+收盘价>前日最低价+前日收阴线"
    "+前日阴线无长上影(1.5倍) 以收盘价买入。"
    "非多头排列：低开3%或收阴线全卖，买入后第一日收盘下跌则清仓，否则卖一半。"
    "多头排列(MA5>MA20>MA60、MA10>MA20>MA60且四线斜率>-1°)："
    "粘合止损(MA5/MA10差值<=2%且开盘破MA10且收阴)、"
    "非粘合缓涨跌破10日线止损，粘合缓涨max(开盘,收盘)<MA10止损，"
    "急涨跌破5日线止损。需 fixed 持仓模式支持部分卖出。",
)
class ShadowYangStrategy(ParametrizedStrategy):
    """影线后收阳线策略（状态机：WAITING -> HOLDING_FULL -> HOLDING_HALF）。

    买入条件（全部满足）：
      1. 当天收阳线（close > open）
      2. MA(price_ma_period) 上行 或 当日为标准反转形态
         - MA 上行：MA 斜率大于 0（ma_now > ma_prev）
         - 标准反转形态（MA 下行时作为替代买入信号）：
           * 刺透形态：当日开盘低于前日最低价，
             收盘高于前日实体中点但低于前日开盘价
           * 看涨吞没：当日阳线实体完全覆盖前日阴线实体
             （开盘 <= 前日收盘，收盘 >= 前日开盘）
           * 启明星：前两日大阴线 + 前一日小实体星线（在前两日实体之下）
             + 当日阳线收盘深入前两日实体
           * 强势反转阳线：当日阳线收盘突破前日实体顶部（收盘 >= 前日开盘），
             且实体 >= 前日阴线实体 × strong_reversal_body_ratio（强势放量反转，允许轻微高开）
      3. 当天收盘价 > 前日最低价（close > prev_low）
      4. 前一天必须收阴线（prev_close < prev_open）
      5. 前一天阴线无长上影线（上影 > 实体 × upper_shadow_ratio 则排除）

    以当天收盘价限价买入。

    卖出规则（持仓后逐日检查）：
      - 非均线多头排列：现有卖出逻辑（优先级从高到低）：
          * 低开（开盘价 <= 前日收盘价 * low_open_pct）：以开盘价全部卖出
          * 收阴线（close < open）：以收盘价全部卖出
          * 买入后第一日收盘下跌（close < 前日收盘价）：以收盘价全部清仓
          * 其余情况：
              - 买入后第一日：以收盘价卖出一半
              - 后续交易日：持有，直到出现阴线或低开时全部卖出
      - 均线多头排列（MA5 > MA20 > MA60、MA10 > MA20 > MA60 且四线斜率角度
        均 > bullish_slope_min；MA5 若低于 MA10 偏离不超过
        bullish_ma5_deviation）：
        均线止损逻辑（优先级从高到低）：
          * 粘合止损：MA5/MA10 粘合（差值 <= ma_convergence_threshold），
            开盘价跌破 10 日均线且当日收阴
          * 缓涨止损（非粘合）：MA(price_ma_period) 斜率角度 < slope_threshold，
            收盘价跌破 ma_stop_period 日均线
          * 缓涨止损（粘合）：MA5/MA10 粘合 且 MA(price_ma_period) 斜率角度
            < slope_threshold，开盘价与收盘价中的最高价跌破
            ma_stop_period 日均线
          * 急涨止损：MA(price_ma_period) 斜率角度 >= slope_threshold，
            收盘价跌破 price_ma_period 日均线

    注意：
      - 使用限价单（``price=`` 参数）在信号当根 K 线成交。
      - 部分卖出需要 ``position_mode="fixed"``，通过类属性
        ``required_position_mode`` 声明，路由层自动传递给引擎。
    """

    # 引擎级需求（注册表自动读取）
    required_position_mode = "fixed"
    default_warmup_bars = 0

    params = [
        Param("price_ma_period", int, default=5, min_value=2, max_value=20, label="价格均线周期"),
        Param(
            "low_open_pct",
            float,
            default=0.97,
            min_value=0.90,
            max_value=1.00,
            label="低开阈值",
            description="开盘价 <= 前收 × 此阈值视为低开（0.97=低开3%）",
        ),
        Param(
            "upper_shadow_ratio",
            float,
            default=1.5,
            min_value=0.5,
            max_value=5.0,
            label="长上影倍数",
            description="前一天阴线上影 > 实体 × 此倍数则不买入（1.5=上影超过实体1.5倍）",
        ),
        Param(
            "strong_reversal_body_ratio",
            float,
            default=2.0,
            min_value=1.0,
            max_value=5.0,
            label="强势反转实体倍数",
            description="强势反转阳线实体 >= 前日阴线实体 × 此倍数（2.0=实体放大2倍）",
        ),
        Param("ma_long_period", int, default=20, min_value=5, max_value=60, label="趋势均线周期"),
        Param(
            "ma_stop_period",
            int,
            default=10,
            min_value=5,
            max_value=30,
            label="缓涨止损均线周期",
        ),
        Param(
            "slope_threshold",
            float,
            default=45.0,
            min_value=0.0,
            max_value=89.0,
            label="MA斜率角度阈值",
            description="MA5 斜率角度阈值（度），< 阈值用长均线止损，>= 阈值用短均线止损",
        ),
        Param(
            "ma_very_long_period",
            int,
            default=60,
            min_value=20,
            max_value=120,
            label="多头排列长均线周期",
        ),
        Param(
            "bullish_slope_min",
            float,
            default=-1.0,
            min_value=-10.0,
            max_value=30.0,
            label="多头排列斜率最小角度",
            description="均线多头排列要求四条均线斜率角度均 > 此值（度），-1.0=几乎不限",
        ),
        Param(
            "bullish_ma5_deviation",
            float,
            default=0.01,
            min_value=0.0,
            max_value=0.05,
            label="MA5偏离MA10最大比例",
            description="MA5 低于 MA10 的最大允许偏离比例（0.01=1%）",
        ),
        Param(
            "ma_convergence_threshold",
            float,
            default=0.02,
            min_value=0.0,
            max_value=0.10,
            label="MA粘合阈值",
            description="MA5/MA10 差值百分比阈值，低于此值视为粘合（0.02=2%）",
        ),
    ]

    # 状态枚举（字符串常量，避免 enum 开销）
    _WAITING = "WAITING"
    _HOLDING_FULL = "HOLDING_FULL"
    _HOLDING_HALF = "HOLDING_HALF"
    _STAR_BODY_RATIO = 0.5  # 启明星星线实体最大比例（占前两日实体）

    def init(self) -> None:
        self.ma = self.I(MA, self.data.close, self.p["price_ma_period"])
        self.ma10 = self.I(MA, self.data.close, self.p["ma_stop_period"])
        self.ma20 = self.I(MA, self.data.close, self.p["ma_long_period"])
        self.ma60 = self.I(MA, self.data.close, self.p["ma_very_long_period"])
        self._state: str = self._WAITING

    def next(self) -> None:
        cur_close = self.data.close[0]
        cur_open = self.data.open[0]
        prev_close = self.data.close[-1]
        i = self._bar_index

        # ── 空仓：寻找买入信号 ───────────────────────────────────────────────
        if self._state == self._WAITING:
            is_yang = cur_close > cur_open
            # MA 斜率：当前 MA > 前一根 MA（NaN 比较为 False，自然跳过预热期）
            ma_now = self.ma[i]
            ma_prev = self.ma[i - 1] if i > 0 else float("nan")
            is_ma_up = ma_now > ma_prev

            # 收盘价必须大于前日最低价（NaN 比较为 False，自然跳过首根 K 线）
            prev_low = self.data.low[-1]
            is_close_above_prev_low = cur_close > prev_low

            # 前一天 OHLC（用于判断阴线 + 上影线 + 刺透形态）
            prev_open = self.data.open[-1]
            prev_high = self.data.high[-1]

            # 前一天必须收阴线（close < open）
            is_prev_yin = prev_close < prev_open

            # 前一天阴线不能有长上影线（上影 > 实体 × upper_shadow_ratio 则排除）
            is_prev_no_long_upper_shadow = not self._is_long_upper_shadow_yin(
                prev_open, prev_high, prev_close, self.p["upper_shadow_ratio"]
            )

            # 刺透形态：MA 下行时作为替代买入信号
            is_piercing_line = self._is_piercing_line(
                cur_open, cur_close, prev_open, prev_close, prev_low
            )

            # 看涨吞没形态：MA 下行时作为替代买入信号
            is_bullish_engulfing = self._is_bullish_engulfing(
                cur_open, cur_close, prev_open, prev_close
            )

            # 启明星形态：MA 下行时作为替代买入信号
            is_morning_star = self._is_morning_star(
                cur_open, cur_close, prev_open, prev_close,
                self.data.open[-2], self.data.close[-2],
            )

            # 强势反转阳线：MA 下行时作为替代买入信号
            is_strong_reversal_yang = self._is_strong_reversal_yang(
                cur_open, cur_close, prev_open, prev_close,
                self.p["strong_reversal_body_ratio"],
            )

            # MA 条件：MA 上行 或 标准反转形态（刺透/吞没/启明星/强势反转）
            is_ma_condition_met = (
                is_ma_up
                or is_piercing_line
                or is_bullish_engulfing
                or is_morning_star
                or is_strong_reversal_yang
            )

            if (
                is_yang
                and is_ma_condition_met
                and is_close_above_prev_low
                and is_prev_yin
                and is_prev_no_long_upper_shadow
            ):
                self.buy(size=0, price=cur_close)
                self._state = self._HOLDING_FULL
            return

        # ── 持仓：检查卖出条件 ──────────────────────────────────────────────
        is_bullish = self._is_ma_bullish(i)

        if is_bullish:
            # 均线多头排列：均线止损逻辑
            ma_now = self.ma[i]
            ma_prev = self.ma[i - 1] if i > 0 else float("nan")
            ma10_now = self.ma10[i]
            angle = self._calc_ma_angle(ma_now, ma_prev)

            should_sell = False

            is_converged = self._is_ma_converged(
                ma_now, ma10_now, self.p["ma_convergence_threshold"]
            )

            # 粘合止损：MA5/MA10 粘合（差值 <= 阈值），开盘价跌破 MA10 且收阴
            if is_converged:
                if cur_open < ma10_now and cur_close < cur_open:
                    should_sell = True

            # 斜率止损（粘合未触发时检查）
            if not should_sell:
                if angle < self.p["slope_threshold"]:
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
                    if cur_close < ma_now:
                        should_sell = True

            if should_sell:
                self.sell(size=0, price=cur_close)
                self._state = self._WAITING
        else:
            # 非均线多头排列：现有卖出逻辑（优先级：低开 > 阴线 > 其余）
            is_low_open = self._is_low_open(cur_open, prev_close, self.p["low_open_pct"])
            is_yin = cur_close < cur_open

            if is_low_open:
                self.sell(size=0, price=cur_open)
                self._state = self._WAITING
            elif is_yin:
                self.sell(size=0, price=cur_close)
                self._state = self._WAITING
            elif self._state == self._HOLDING_FULL:
                if cur_close < prev_close:
                    # 买入后第一日收盘下跌：以收盘价全部清仓
                    self.sell(size=0, price=cur_close)
                    self._state = self._WAITING
                else:
                    # 以收盘价卖一半（向下取整到 100 股整手）
                    position_size = self.position["size"]
                    half = int(position_size / 2 / 100) * 100
                    if half > 0:
                        self.sell(size=half, price=cur_close)
                    self._state = self._HOLDING_HALF
            # HOLDING_HALF + 其余情况：持有，不做操作

    @staticmethod
    def _is_low_open(cur_open: float, prev_close: float, threshold: float) -> bool:
        """判断是否低开（开盘价 <= 前日收盘价 × 阈值）。

        NaN 检查：``prev_close == prev_close`` 对 NaN 返回 False。
        """
        if not (prev_close == prev_close and prev_close > 0):
            return False
        return cur_open <= prev_close * threshold

    @staticmethod
    def _is_long_upper_shadow_yin(
        prev_open: float, prev_high: float, prev_close: float, ratio: float
    ) -> bool:
        """判断前一天阴线是否有长上影线（上影 > 实体 × ratio）。

        仅当 prev_close < prev_open（阴线）时判断；阳线返回 False（放行）。
        NaN 输入返回 False。
        """
        if not (prev_open == prev_open and prev_high == prev_high and prev_close == prev_close):
            return False
        if prev_close >= prev_open:
            return False
        body = prev_open - prev_close
        upper_shadow = prev_high - prev_open
        return upper_shadow > body * ratio

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
          2. 前一日小实体星线（|prev_open - prev_close| < 前两日实体 ×
             STAR_BODY_RATIO），星线实体在前两日实体之下
             （max(prev_open, prev_close) < prev2_close）
          3. 当日阳线（cur_close > cur_open），收盘价深入前两日实体
             （cur_close > 前两日实体中点）

        NaN 输入返回 False。
        """
        values = (cur_open, cur_close, prev_open, prev_close, prev2_open, prev2_close)
        if not all(v == v for v in values):
            return False
        if prev2_close >= prev2_open:
            return False
        prev2_body = prev2_open - prev2_close
        prev_body = abs(prev_open - prev_close)
        if prev_body >= prev2_body * ShadowYangStrategy._STAR_BODY_RATIO:
            return False
        if max(prev_open, prev_close) >= prev2_close:
            return False
        if cur_close <= cur_open:
            return False
        midpoint = (prev2_open + prev2_close) / 2
        return cur_close > midpoint

    @staticmethod
    def _is_strong_reversal_yang(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
        body_ratio: float,
    ) -> bool:
        """判断当日是否为强势反转阳线（Strong Reversal Yang）。

        条件（全部满足）：
          1. 前一日阴线（prev_close < prev_open）
          2. 当日阳线（cur_close > cur_open）
          3. 当日收盘价 >= 前日开盘价（cur_close >= prev_open，收盘突破前日实体顶部）
          4. 当日实体 >= 前日实体 × body_ratio（实体至少放大 body_ratio 倍）

        与看涨吞没的区别：不要求开盘 <= 前日收盘价（允许轻微高开），
        但要求实体明显放大，确保是强势反转而非弱反弹。

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
        return cur_body >= prev_body * body_ratio

    @staticmethod
    def _is_ma_converged(ma5: float, ma10: float, threshold: float) -> bool:
        """判断 MA5 和 MA10 是否粘合（差值百分比 <= threshold）。

        以 MA10 为基准计算差值百分比：|MA5 - MA10| / MA10。
        NaN 或除零返回 False。
        """
        if ma5 != ma5 or ma10 != ma10 or ma10 <= 0:
            return False
        return abs(ma5 - ma10) / ma10 <= threshold

    def _is_ma_bullish(self, i: int) -> bool:
        """判断是否为均线多头排列。

        条件：
          1. MA5 > MA20 > MA60 且 MA10 > MA20 > MA60（多头排列）；
             MA5 若低于 MA10，偏离不超过 bullish_ma5_deviation
          2. MA5/MA10/MA20/MA60 斜率角度均 > bullish_slope_min（均线上行）

        NaN 值时比较返回 False，自然跳过预热期。
        """
        if i < 1:
            return False

        ma5 = self.ma[i]
        ma10 = self.ma10[i]
        ma20 = self.ma20[i]
        ma60 = self.ma60[i]

        # 多头排列：MA5 > MA20 > MA60 且 MA10 > MA20 > MA60
        if not (ma5 > ma20 > ma60 and ma10 > ma20 > ma60):
            return False

        # MA5 若低于 MA10，偏离不超过 bullish_ma5_deviation
        deviation = self.p["bullish_ma5_deviation"]
        if ma5 < ma10 and ma5 < ma10 * (1 - deviation):
            return False

        # 四条均线斜率角度均 > 阈值
        slope_min = self.p["bullish_slope_min"]
        for ma_now, ma_prev in (
            (self.ma[i], self.ma[i - 1]),
            (self.ma10[i], self.ma10[i - 1]),
            (self.ma20[i], self.ma20[i - 1]),
            (self.ma60[i], self.ma60[i - 1]),
        ):
            if self._calc_ma_angle(ma_now, ma_prev) <= slope_min:
                return False

        return True

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
