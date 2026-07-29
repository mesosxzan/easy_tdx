"""缠论三类买点策略。

理论基础
========
缠论三类买点（3buy）：价格向上突破中枢（ZhongShu）后回调，
回调低点不跌破中枢上沿（zg），形成的新底分型即为三类买点。

本策略仅做第三类买点，不参与一类、二类买点。

信号确认机制（防前瞻偏差）
============================
缠论分析一次性处理全量 K 线，但实盘只能使用已确认的信号。本策略
通过以下机制消除前瞻偏差：

1. **确认 bar 替代信号 bar**：分型由 3 根 CLKline [左,中,右] 构成，
   仅当右侧 CLKline 完成时分型才确认。策略使用 ``bi.end.klines[2].k_index``
   （确认 bar）而非 ``bi.end.k.k_index``（中间 CLKline bar）作为执行时机。

2. **过滤最后笔信号**：``find_bis`` 一次性处理全量数据，最后一笔的
   端点可能随新 K 线到来而变化（分型替换/延伸）。最后笔的 MMD 信号
   被排除。

3. **硬止损安全网**：默认 10% 固定止损，防极端亏损。

买入条件
========
  - 缠论分析识别出第三类买点（BUY_3）
  - 以确认 bar 当根 K 线收盘价买入（限价单，引擎延迟到下一根开盘成交）

卖出条件（可配置，全部满足任一即卖出）
========================================
  1. 固定止损：收盘价 <= 买入价 × (1 - STOP_LOSS_PCT/100)
  2. 固定止盈：收盘价 >= 买入价 × (1 + TAKE_PROFIT_PCT/100)
  3. 均线止损：收盘价跌破 MA_EXIT_PERIOD 日均线
  4. 移动止损：收盘价 <= 持仓最高价 × (1 - TRAILING_STOP_PCT/100)
  5. 最大持仓：持仓天数 >= MAX_HOLD_DAYS
  6. 缠论卖点：出现 1sell / 2sell / 3sell 信号（同样使用确认 bar）

参数为 0 或 False 时禁用对应条件。

用法（CLI）
===========
::

    easy-tdx backtest 600519 \\
        --strategy-file strategies/chanlun_3buy.py \\
        --chanlun-level DAILY \\
        --position-mode full \\
        --warmup-bars 60 \\
        --count 800 \\
        --adjust QFQ \\
        --table

用法（Python API）
===================
::

    from easy_tdx.backtest import BacktestEngine

    engine = BacktestEngine(
        strategy=Chanlun3BuyStrategy,
        cash=100000,
        chanlun_level="DAILY",
        warmup_bars=60,
    )
    result = engine.run(df)
    print(result.performance["total_return"])
"""

from __future__ import annotations

from typing import Any

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy
from easy_tdx.chanlun.types import MMDType

# ── 可调参数（优化脚本可通过类属性覆盖） ─────────────────────────────────────

STOP_LOSS_PCT: float = 10.0  # 固定止损百分比（安全网，防止极端亏损）
TAKE_PROFIT_PCT: float = 0.0  # 固定止盈百分比（0 = 禁用）
MA_EXIT_PERIOD: int = 0  # 均线止损周期（0 = 禁用）
TRAILING_STOP_PCT: float = 0.0  # 移动止损百分比（0 = 禁用）
MAX_HOLD_DAYS: int = 15  # 最大持仓天数（0 = 禁用）
USE_CHANLUN_SELL: bool = True  # 是否使用缠论卖点信号
USE_ZS_STOP: bool = False  # 使用中枢下沿(dd)作为止损位（优先于固定止损）
VOLUME_CONFIRM: bool = False  # 突破笔量能确认（突破笔成交量 > 前20根均量）
MIN_SIGNAL_GAP: int = 0  # 买入信号最小间隔K线数（0 = 禁用，防止过度交易）

# ── 状态枚举 ─────────────────────────────────────────────────────────────────

_WAITING = "WAITING"  # 空仓等待 3buy 信号
_HOLDING = "HOLDING"  # 持仓中，检查卖出条件


class Chanlun3BuyStrategy(Strategy):
    """缠论三类买点策略。

    状态机::

        WAITING --3buy信号--> HOLDING --卖出条件--> WAITING
    """

    # ── 类级参数（优化脚本可直接覆盖） ───────────────────────────────────────
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    ma_exit_period: int = MA_EXIT_PERIOD
    trailing_stop_pct: float = TRAILING_STOP_PCT
    max_hold_days: int = MAX_HOLD_DAYS
    use_chanlun_sell: bool = USE_CHANLUN_SELL
    use_zs_stop: bool = USE_ZS_STOP
    volume_confirm: bool = VOLUME_CONFIRM
    min_signal_gap: int = MIN_SIGNAL_GAP

    def init(self) -> None:
        # 均线指标（用于均线止损）
        if self.ma_exit_period > 0:
            self.ma_exit = self.I(MyTT.MA, self.data.close, self.ma_exit_period)
        else:
            self.ma_exit = None

        # 量能指标（用于突破笔量能确认）
        if self.volume_confirm:
            self._avg_vol = self.I(MyTT.MA, self.data.vol, 20)
        else:
            self._avg_vol = None

        # 从缠论结果中提取 3buy 信号对应的 K 线索引
        self._buy_signal_bars: set[int] = set()
        self._sell_signal_bars: set[int] = set()
        # 存储 bar_idx -> MMD 对象映射（用于中枢止损）
        self._buy_mmd_by_bar: dict[int, Any] = {}

        # 持仓状态（提前初始化，确保所有路径一致）
        self._state: str = _WAITING
        self._buy_price: float = 0.0
        self._highest_since_buy: float = 0.0
        self._buy_bar_index: int = 0
        self._zs_stop_price: float = 0.0  # 中枢止损价
        self._last_signal_bar: int = -9999  # 上次信号bar（用于信号间隔过滤）

        chanlun = self.chanlun
        if chanlun is None:
            self._diagnostic = (
                "缠论策略未注入分析数据，0 交易。请添加 --chanlun-level DAILY 参数。\n"
                "示例: easy-tdx backtest 601666 "
                "--strategy-file strategies/chanlun_3buy.py --chanlun-level DAILY"
            )
            return

        result = _extract_chanlun_result(chanlun)
        if result is not None:
            # ── 优先使用增量分析器的 signals_by_bar ──────────────────────
            # IncrementalAnalyser 逐K线推进，signals_by_bar 记录每个确认bar
            # 上新确认的MMD信号。信号bar本身就是确认bar（已消除前瞻偏差），
            # 无需再计算 confirm_bar 或过滤最后笔。
            #
            # 优化后的MMD检测包含：
            # - MACD力度比较（替代幅度比较）
            # - MACD回拉零轴过滤（弱中枢信号被过滤）
            # - 3buy突破笔验证（前一笔必须是向上突破笔）
            # - 趋势上下文过滤
            signals_by_bar = getattr(result, "signals_by_bar", None)
            if signals_by_bar:
                for bar_idx, mmds in signals_by_bar.items():
                    for mmd in mmds:
                        if mmd.mmd_type == MMDType.BUY_3:
                            self._buy_signal_bars.add(bar_idx)
                            self._buy_mmd_by_bar[bar_idx] = mmd
                        elif self.use_chanlun_sell and mmd.mmd_type in (
                            MMDType.SELL_1,
                            MMDType.SELL_2,
                            MMDType.SELL_3,
                        ):
                            self._sell_signal_bars.add(bar_idx)
                return

            # ── 回退：批量分析器的信号提取（向后兼容） ─────────────────────
            # 过滤最后一条笔的 MMD：find_bis 一次性处理全量数据，
            # 最后一笔的端点可能随新 K 线到来而变化（分型替换/延伸），
            # 由此产生的 MMD 信号不可靠，必须排除。
            last_bi_index = len(result.bis) - 1 if result.bis else -1
            filtered_count = 0

            for mmd in result.mmds:
                if mmd.bi is None:
                    continue
                # 跳过最后一条笔的信号（未确认，可能变化）
                if mmd.bi.index == last_bi_index:
                    filtered_count += 1
                    continue

                # 使用分型确认 bar（右侧 CLKline 的最后一根原始 K 线 index）
                # 而非笔端点 bar（中间 CLKline 的 index）。
                #
                # 分型由 3 根 CLKline [左, 中, 右] 构成，只有当右侧 CLKline
                # 完成时分型才确认。之前使用 bi.end.k.k_index（中间 CLKline）
                # 导致在分型未确认时就执行交易——典型的前瞻偏差。
                confirm_bar = _get_confirm_bar(mmd.bi)

                if mmd.mmd_type == MMDType.BUY_3:
                    self._buy_signal_bars.add(confirm_bar)
                elif self.use_chanlun_sell and mmd.mmd_type in (
                    MMDType.SELL_1,
                    MMDType.SELL_2,
                    MMDType.SELL_3,
                ):
                    self._sell_signal_bars.add(confirm_bar)

            if filtered_count > 0:
                self._diagnostic = (
                    f"已过滤 {filtered_count} 条来自最后一笔的未确认信号（防前瞻偏差）。"
                )

    def next(self) -> None:
        cur_close = self.data.close[0]
        i = self._bar_index

        if self._state == _WAITING:
            if i in self._buy_signal_bars:
                # 信号间隔过滤
                if self.min_signal_gap > 0 and i - self._last_signal_bar < self.min_signal_gap:
                    return

                # 量能确认：突破笔成交量需大于20日均量
                if self.volume_confirm and self._avg_vol is not None:
                    cur_vol = self.data.vol[0]
                    avg_v = self._avg_vol[i] if i < len(self._avg_vol) else 0.0
                    if avg_v == avg_v and cur_vol < avg_v:
                        return  # 量能不足，放弃信号

                self.buy(size=0, price=cur_close, reason="缠论三类买点")
                self._state = _HOLDING
                self._buy_price = cur_close
                self._highest_since_buy = cur_close
                self._buy_bar_index = i
                self._last_signal_bar = i

                # 中枢止损：从MMD对象获取中枢dd作为止损价
                if self.use_zs_stop:
                    mmd = self._buy_mmd_by_bar.get(i)
                    if mmd is not None and mmd.zs is not None:
                        self._zs_stop_price = mmd.zs.dd
                    else:
                        self._zs_stop_price = 0.0  # 无中枢信息，回退固定止损
                else:
                    self._zs_stop_price = 0.0
            return

        # 持仓中：更新最高价，检查卖出条件
        if cur_close > self._highest_since_buy:
            self._highest_since_buy = cur_close

        if self._check_exit(cur_close, i):
            self.sell(size=0, price=cur_close, reason="卖出条件触发")
            self._state = _WAITING

    def _check_exit(self, cur_close: float, bar_index: int) -> bool:
        """检查是否满足任一卖出条件。"""
        # 0. 中枢止损（优先于固定止损，使用中枢dd作为止损位）
        if self.use_zs_stop and self._zs_stop_price > 0:
            if cur_close <= self._zs_stop_price:
                return True

        # 1. 固定止损（中枢止损未触发或未启用时的安全网）
        if self.stop_loss_pct > 0 and self._buy_price > 0:
            stop_price = self._buy_price * (1 - self.stop_loss_pct / 100)
            if cur_close <= stop_price:
                return True

        # 2. 固定止盈
        if self.take_profit_pct > 0 and self._buy_price > 0:
            profit_price = self._buy_price * (1 + self.take_profit_pct / 100)
            if cur_close >= profit_price:
                return True

        # 3. 均线止损
        if self.ma_exit is not None and bar_index < len(self.ma_exit):
            ma_val = self.ma_exit[bar_index]
            if ma_val == ma_val and cur_close < ma_val:  # NaN 检查
                return True

        # 4. 移动止损
        if self.trailing_stop_pct > 0 and self._highest_since_buy > 0:
            trailing_stop = self._highest_since_buy * (1 - self.trailing_stop_pct / 100)
            if cur_close <= trailing_stop:
                return True

        # 5. 最大持仓天数
        if self.max_hold_days > 0:
            hold_days = bar_index - self._buy_bar_index
            if hold_days >= self.max_hold_days:
                return True

        # 6. 缠论卖点
        if self.use_chanlun_sell and bar_index in self._sell_signal_bars:
            return True

        return False


def _extract_chanlun_result(chanlun: Any) -> Any:
    """从 self.chanlun 提取 ChanlunResult 对象。

    chanlun 可能是 ChanlunResult（引擎注入）或 dict（Web 层序列化）。
    引擎注入的是原始 ChanlunResult 对象，直接返回。
    """
    # ChanlunResult 有 mmds 属性
    if hasattr(chanlun, "mmds"):
        return chanlun
    return None


def _get_confirm_bar(bi: Any) -> int:
    """获取笔的确认 bar index。

    笔的端点分型由 3 根 CLKline [左, 中, 右] 构成。
    分型只有在右侧 CLKline 完成时才确认，因此确认 bar 是
    ``bi.end.klines[2].k_index``（右侧 CLKline 的最后一根原始 K 线 index）。

    Args:
        bi: 笔对象（BI dataclass）

    Returns:
        确认 bar 的原始 K 线 index
    """
    end_fx = bi.end
    if hasattr(end_fx, "klines") and len(end_fx.klines) >= 3:
        return int(end_fx.klines[2].k_index)
    # 安全兜底：退化到中间 CLKline（不应发生，保持向后兼容）
    return int(end_fx.k.k_index)
