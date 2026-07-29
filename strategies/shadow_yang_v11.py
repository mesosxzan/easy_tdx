"""影线后收阳线策略 V2（优化版）。

基于 shadow_yang.py 的回测数据驱动优化。本次优化（出场端优化 #22）::

  数据驱动分析（50 只个股，139 笔交易，81 胜 58 负，胜率 58.3%，PF 6.18）::

    入场端因子分析（52 列全量指标 + t 检验）发现：
    - 所有单因子/组合入场过滤均无法提升总收益。策略 PF=6.18 已经很强，
      即使"差"的入场也是正期望值（被过滤交易的 PF 仍达 1.4-5.4）。
    - 盈利交易 avg +14.82% >> 亏损交易 avg -3.40%，任何过滤移除的盈利
      总额远超移除的亏损总额，入场过滤方向无效。

    转向出场端优化，聚焦盈利锁定和追踪止盈：

  本次优化（出场端）：
   22. 强趋势盈利锁定豁免：ADX>=30 多头强趋势时跳过 Tier1(3%) 和 Tier2(6%)
       盈利锁定，从 Tier3(10%) 开始锁。强趋势中 3-7% 回调是正常洗盘，
       过早锁定会截断趋势延续。盈利锁定 32 笔 avg +1.66%（vs 止盈 avg +23.1%），
       部分被锁定的交易在强趋势中被过早截断。
   23. 超大幅盈利追踪放宽：浮盈 > 25% 时追踪宽度从 2.0×ATR 放宽到 2.5×ATR，
       让超大趋势单充分奔跑。原 >15% 统一用 2.0×ATR 可能截断 mega-trend。

基于 shadow_yang.py 的回测数据驱动优化。本次优化（随机样本因子分析 V2）::

  数据驱动分析（50 只个股，188 笔交易，5 组 wencai 随机样本，
  92 胜 96 负，胜率 48.9%，PF 3.67）::

    对 5 组问财随机查询（活跃股/中盘价值/强势/低价/高换手率）的 50 只个股
    进行全量因子分析，在已有 CCI/MFI/BOLL 超买过滤基础上，识别 3 项新过滤因子。

  本次优化（在 CCI/MFI/BOLL 过滤之上叠加）：
   18. 新增下影线过长过滤（下影线 > 实体 × 1.0 时不买入）
       因子分析：18 笔仅 16.7% 胜率（vs 52.4%），均收益 -1.91%。
       反转策略中长下影线看似支撑强，实为下跌中继假支撑。
   19. 新增 KDJ-J 极端超买过滤（J > 100 时不买入）
       3 笔全部亏损（0% 胜率），均 -1.64%。未被 CCI/MFI/BOLL 捕获的独立超买维度。
   20. 新增 BOLL %b 高位区间过滤（非强趋势 + 影线后收阳 + %b 0.8-1.0 时不买入）
       15 笔仅 20% 胜率。仅过滤影线后收阳路径，保留多头排列形成（50% 胜率）
       和强趋势中的交易（含 +93%/+49% 大盈利单）。

基于 shadow_yang.py 的回测数据驱动优化。本次优化（多股超买因子过滤）::

  数据驱动分析（53 只个股，400 笔交易，169 胜 231 负，胜率 42.2%，PF 2.30）::

    买入日全量技术指标提取（RSI/MACD/KDJ/BOLL/CCI/WR/MFI + ADX/BIAS/MA偏离）
    + 盈亏配对 + 分桶胜率分析，识别超买区入场为亏损主因：

  诊断要点：
    - CCI > 100（194 笔，49% 交易）：胜率 36.1%，PF 1.66
      CCI 衡量价格偏离均值程度，>100 = 超买扩张。强趋势(36.5%)/非强趋势(35.3%)
      均低于基准，不豁免。CCI <= 100 的 206 笔交易胜率 48.1%，PF 3.30。
    - MFI > 80（23 笔）：胜率 30.4%，均收益 -1.55%
      MFI 是带量 RSI，>80 = 资金流入过度。资金流超买是追高的可靠信号。
    - BOLL %b > 1.0 + 非强趋势（34 笔）：胜率 26.5%
      %b > 1.0 = 收盘价突破布林上轨。非强趋势中突破上轨多为冲高回落假突破；
      强趋势中 43%（接近基准），仅非强趋势时过滤。

  本次优化：
   15. 新增 CCI 超买过滤（CCI > 100 时不买入）
       多股因子分析：CCI > 100 在强/弱趋势中胜率均仅 36%（vs 基准 42.2%），
       是最有效的单一过滤因子。过滤后剩余 206 笔交易胜率 48.1%，PF 3.30。
   16. 新增 MFI 超买过滤（MFI > 80 时不买入）
       MFI > 80 胜率 30.4%，均收益 -1.55%。资金流超买与价格超买不同步时
       尤为危险（放量追高），MFI 直接衡量资金流入强度。
   17. 新增 BOLL %b 超买过滤（非强趋势时 %b > 1.0 不买入）
       BOLL %b > 1.0 在非强趋势中胜率仅 26.5%（强趋势 43% 接近基准）。
       非强趋势中突破布林上轨多为假突破/冲高回落，强趋势中是正常趋势延续。

基于 shadow_yang.py 的回测数据驱动优化。本次优化（多股买入特征分析）::

  数据驱动分析（8 只个股，84 笔交易，32 胜 52 负，胜率 38.1%）::

    买入日 K 线特征提取 + 盈亏配对，识别关键区分特征：

  诊断要点：
    - 刺穿形态（is_piercing_line）18 笔交易 0% 胜率——全部亏损
      刺穿形态要求 cur_close < prev_open（未完全收复前日跌幅），
      反转力度不足。与看涨吞没/强反转阳线互斥（后者要求 cur_close >= prev_open）
    - 晨星形态（is_morning_star）6 笔交易 0% 胜率——全部亏损
      中间日实体小（犹豫不决），反转可靠性低
    - ADX 15-30 中等趋势区间胜率仅 17%（5 胜 24 负）——最大亏损来源
      ADX<15 胜率 71%（无趋势=反转可靠），ADX>=40 胜率 50%（强趋势=延续可靠）
      中等趋势下趋势足以延续但策略误判为反转

  本次优化：
   13. 移除刺穿形态和晨星形态买入条件（0% 胜率，24 笔全亏）
       从 is_ma_condition_met 的 OR 条件中移除 is_piercing_line 和 is_morning_star，
       保留 is_ma5_up / is_bullish_engulfing / is_strong_reversal_yang 三个条件
   14. 新增 ADX 中等趋势过滤（15 <= ADX < 30 时跳过买入）
       例外：强反转阳线（实体 >= 前日阴线 × 2.0）可突破此过滤，
       因为强反转意味着买盘完全收复前日跌幅，即使在中等趋势中也可靠

基于 shadow_yang.py 的回测数据驱动优化（针对 600206 回测诊断）::

  诊断要点（fixed 模式，1000 根 K 线）：
    - 持仓 0-2 天胜率 0%、2-5 天胜率 16%，而 20 天+胜率 100% 均赚 23%
    → 卖出过于敏感，把起飞前的趋势单全震出去（截断利润）
    - 回踩 MA10 买入信号胜率仅 12%、均 -2.3%（负期望拖累）
    - 2022 熊市 6 笔全亏 → MA20 过滤不足以阻挡下跌中继抄底
    - full 模式下半仓逻辑失效（_sell_half 卖出全部），状态机错乱

  本次优化：
    1. 移除半仓逻辑（_sell_half / HOLDING_HALF）：全仓进出，修复 full 模式
       position_mode 不匹配的致命 bug（orders.py 的 full 模式忽略 sell size）
    2. 移除回踩 MA10 买入（胜率 12% 负期望）与浮盈加仓（full 模式行为异常）
    3. 加强趋势过滤：MA60 近 20 日不可下行，避免长期下跌中继抄底
    4. 重写卖出：分阶段追踪止盈（浮盈越大追踪越宽→让趋势单跑起来），
       以 ATR 硬止损为底线，移除「第一日下跌减半」这一短持仓主因
    5. 震荡市快速周转：ADX < 20（无方向震荡）时收紧追踪止盈（浮盈 >= 3%
       即追踪、宽度 1.5×ATR），避免震荡上行股中利润回吐；趋势市保持宽追踪
    6. 强趋势追涨许可：ADX >= 30 且 +DI > -DI（多头强趋势）时放宽追高保护
       （上影线阈值 2.0、近 3 日涨幅 18%、近 5 日涨幅 30%、偏离 MA20 35%），
       避免主升浪起飞阶段被追高保护过滤阻断；非强趋势保持原参数
    7. MA20 下方回调反转买入：股价 < MA20 但 > MA60 且出现强反转阳线
       （实体 >= 前日阴线 × 2.0）时允许买入，捕捉 MA20 附近的回调反转机会。
       附加过滤：MA20 必须走平或上行（避免下跌中继抄底）；MA60 方向过滤
       仍会执行，确保中长期趋势向上。不接受单纯看涨吞没（实体比例无要求，
       在 MA20 下方回调场景中假信号率更高）
    8. 强趋势动态阴线止损放宽：非多头排列持仓中，ADX>=30 且 +DI>-DI
       （多头强趋势）时，动态阴线止损阈值从 1.2×ATR% 放宽到 2.0×ATR%
       （最低 3.0% vs 2.0%），避免主升浪中的正常单日回调被清出。
       002156 诊断：ADX=86.1 时 4.46% 阴线回调被原 1.2×ATR%(3.82%)
       清出，错过后续 +39.8% 主升浪。非强趋势保持原参数。
    9. 多头排列放宽近3日涨幅追高保护：均线多头排列（MA5>MA20>MA60 且
       MA10>MA20>MA60）时，近3日涨幅上限从 12% 放宽到 18%（与强趋势一致）。
       600176 诊断：2026-06-11 多头排列首次形成日，近3日涨幅 15.49%（V 型
       反弹：06-08 大跌 -3.8% 后三连阳反弹），ADX=24.2 不满足强趋势门槛，
       被原 12% 上限拦截，错过后续 06-17 +32.9% 主升浪。多头排列=趋势确认，
       V 型反弹涨幅非冲顶追高，放宽合理。非多头排列保持原参数。

  600539 专项优化（均线多头排列 + 粘合方向）::
    诊断要点：600539 是大牛股（买入持有 +378%），但策略 -8.27%，7 笔仅 1 笔盈利。
    根因：主升浪期间大量连续阳线，无"前日阴线"形态，策略完全错过；多头排列中
    止损过于敏感，趋势单被震出。

    9. 多头排列首次形成买入信号（方案 C）：当 MA5>MA20>MA60 且 MA10>MA20>MA60
       多头排列首次形成（前一天不满足）时买入，不依赖"前日阴线"形态，捕捉主升浪
       起点。回测诊断：该信号胜率 57.1%，平均收益 6.96%。600539 新增 3 笔交易
       2 笔盈利，收益从 -8.27% 提升到 +4.83%。所有过滤条件（MA60 方向、追高保护、
       成交量、急跌弱反弹）已在上游执行，多头排列中 MA20 必然上行故 ADX 震荡市
       过滤不会误杀。
   10. 多头排列中禁用震荡市时间止损（方案 A）：多头排列说明趋势已形成，ADX 低
       只是趋势起步阶段，不应因 ADX<20 就时间止损清出。600539 #2 多头排列+
       ADX=12.8 被震荡市时间止损清出，卖后涨 52%。
   11. 卖出确认 pending 清除修复（方案 B）：多头排列中 should_sell=False 时清除
       _sell_signal_pending，避免首次触发卖出信号后 pending 永远保持 True，
       导致后续任意触发都直接卖出（延迟 N 天的假确认）。600539 #4 05-14 首次
       should_sell=True 设 pending，05-15~05-20 should_sell=False 但 pending
       未清除，05-21 should_sell=True 时直接卖出（延迟 7 天触发）。
   12. 多头排列中动态阴线止损阈值放宽（方案 D）：多头排列中阴线止损 ATR 倍数
       从 1.2 放宽到 1.5（最低 2.5% vs 2.0%），避免趋势中正常回调被清出。
       600539 #2 多头排列+ADX=12.8，12-17 阴线跌 7.2% 被原 1.2×ATR%(6.0%)
       清出，卖后涨 113%。
   13. 盈利锁定阶梯（ratchet mechanism）：基于最高浮盈逐步锁定利润下限。
       MAE/MFE 分析（68 笔交易）发现 17 笔盈转亏交易（占亏损 46%）曾盈利
       3-21% 但最终亏损，18 笔低效赢家仅捕获 MFE 的 36.7%。根因是浮盈 0-5%
       区间无追踪保护 + 5-15% 追踪过松（3×ATR 在高 ATR 股中追踪宽度达 27%）。
       盈利锁定以最高浮盈为基准，达到触发阈值即锁定利润（3%->保本, 6%->2%,
       10%->4%, 15%->7%, 20%->10%），只升不降，防止利润全部回吐甚至转亏。

基于 shadow_yang.py 的早期优化（保留）：
  - 卖出规则：连续 2 日阴线或单日阴线跌幅 > 1.2×ATR% 才卖出 + 追踪止盈
  - 买入趋势过滤：收盘价须在 MA20 之上 + 连续止损后冷却
  - 追高保护：偏离 MA20 超过 25% 或近 5 日涨幅 > 20% 不买入
  - 成交量确认：买入日成交量 > 5 日均量 × 1.0
  - ATR 动态止损：止损 = 买入价 - 2.2×ATR
  - 多头排列持仓加强：浮盈 > 10% 时止损放宽到跌破 MA20

用法（CLI）::

    easy-tdx backtest 600206 --strategy-file strategies/shadow_yang_v2.py

注：本策略全仓进出，full / fixed 模式均可正确工作。
"""

import math

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy

# ── 常量 ─────────────────────────────────────────────────────────────────────

LOW_OPEN_THRESHOLD = 0.97
UPPER_SHADOW_RATIO = 1.5
STRONG_TREND_SHADOW_RATIO = 2.0  # 强趋势时上影线阈值放宽（趋势中上影线常见，不视为出货）
STAR_BODY_RATIO = 0.5
STRONG_REVERSAL_BODY_RATIO = 2.0
MA_LONG_PERIOD = 20
MA_VERY_LONG_PERIOD = 60
MA_STOP_PERIOD = 10
SLOPE_THRESHOLD_DEG = 45.0
MA_BULLISH_MA5_DEVIATION = 0.01
MA_CONVERGENCE_THRESHOLD = 0.02

# ── V2 新增常量 ──────────────────────────────────────────────────────────────

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.2  # 硬止损倍数
MAX_LOSS_PCT = 8.0  # 最大亏损保护：亏损超过 8% 强制止损
MA20_DEVIATION_MAX = 0.25  # 追高保护：偏离 MA20 上限
RECENT_SURGE_DAYS = 5
RECENT_SURGE_MAX = 0.20  # 追高保护：近 5 日涨幅上限
SHORT_SURGE_DAYS = 3  # 追高保护（短期）：近 3 日涨幅窗口
SHORT_SURGE_MAX = 0.12  # 追高保护（短期）：近 3 日涨幅上限

# ── 强趋势追涨许可（ADX 确认强趋势时放宽追高保护）────────────────────────────
# 002407 回测诊断：2026-06~07 主升浪（+53%）完全错过，根因是起飞阶段连续阳线
# 不满足"前日阴线"形态，且少数有形态信号的日期（06-24 吞没+强反转）被长上影线
# (ratio 1.625>1.5) 和近 3 日涨幅(14.1%>12%) 过滤阻断。
# 但此时 ADX=53.9（强趋势），高涨幅/高偏离是趋势延续而非冲顶，不应过滤。
# 方案：ADX>=30 且 +DI>-DI（多头强趋势）时放宽追高保护，非强趋势保持原参数。
STRONG_TREND_ADX = 30  # 强趋势 ADX 阈值（与 dmi_trend.py 趋势确认一致）
STRONG_TREND_SURGE3_MAX = 0.18  # 强趋势时近 3 日涨幅上限（vs 0.12）
STRONG_TREND_SURGE5_MAX = 0.30  # 强趋势时近 5 日涨幅上限（vs 0.20）
STRONG_TREND_DEV_MAX = 0.35  # 强趋势时偏离 MA20 上限（vs 0.25）
STRONG_TREND_YIN_DROP_RATIO = 2.0  # 强趋势时阴线止损 ATR 倍数（vs 1.2，避免主升浪回调被震出）
STRONG_TREND_YIN_MIN_PCT = 3.0  # 强趋势时阴线止损最低百分比（vs 2.0）
# 空头强趋势过滤：-DI >= +DI * RATIO 时判定为空头强趋势
# 600539 2025-10-10: -DI/+DI=2.19（空头远强），2 天后低开止损，应过滤
# 002407 2026-02-11: -DI/+DI=1.32（空头略强），后续盈利 +1888，不应过滤
BEAR_STRONG_TREND_DI_RATIO = 2.0  # 空头力量达多头 2 倍以上才过滤

# 多头排列中动态阴线止损阈值（比非多头排列的 1.2 放宽，避免趋势中正常回调被清出）
# 600539 诊断：#2 多头排列+ADX=12.8，12-17 阴线跌 7.2% 被原 1.2×ATR%(6.0%)
# 清出，卖后涨 113%。多头排列是趋势确认，阴线更可能是洗盘而非反转
BULLISH_YIN_DROP_ATR_RATIO = 1.5  # 多头排列中阴线止损 ATR 倍数（vs 1.2）
BULLISH_YIN_MIN_PCT = 2.5  # 多头排列中阴线止损最低百分比（vs 2.0）
VOLUME_RATIO_MIN = 1.0  # 成交量确认（不低于均量即可）
COOLDOWN_BARS = 5
MAX_CONSECUTIVE_LOSSES = 2
PROFIT_RELAX_THRESHOLD = 0.10  # 浮盈 > 10% 放宽止损到 MA20

# ── 分阶段追踪止盈（让趋势单跑起来）──────────────────────────────────────
# 回测显示持仓 20 天+胜率 100%，短持仓全亏。根因是追踪止盈太紧（7%）+ 第一日
# 减半，把起飞前的单子震出去。改为：浮盈小时给足空间（只靠硬止损），浮盈大时
# 逐步收紧锁定利润。
PROFIT_TIER2 = 0.05  # 浮盈 >= 5% 启用追踪止盈
PROFIT_TIER3 = 0.15  # 浮盈 >= 15% 收紧追踪（锁定大利润）
PROFIT_TIER4 = 0.25  # 浮盈 >= 25% 超大幅盈利追踪（放宽，让超大趋势跑）
ATR_TRAIL_WIDE = 3.0  # 浮盈 5%-15%：宽追踪（让波动，避免被震出）
ATR_TRAIL_TIGHT = 2.0  # 浮盈 >15%：收紧追踪（锁定大部分利润）
ATR_TRAIL_MEGA = 2.5  # 浮盈 >25%：放宽追踪（让超大趋势充分奔跑）
TRAILING_STOP_PCT_MAX = 12.0  # 百分比追踪上限（与 ATR 追踪取较宽者）

# ── 盈利锁定阶梯（ratchet mechanism）──────────────────────────────────────
# MAE/MFE 分析（68 笔交易）：17 笔盈转亏交易（占亏损 46%）曾盈利 3-21% 但最终
# 亏损，根因是浮盈 0-5% 区间无追踪保护 + 5-15% 追踪过松（3×ATR 在高 ATR 股中
# 追踪宽度可达 27%）。18 笔低效赢家仅捕获 MFE 的 36.7%，大量利润回吐。
#
# 盈利锁定以最高收盘浮盈为基准（非最高价，过滤盘中尖峰噪音），一旦收盘达到
# 触发阈值即将止损锁定在对应水平，只升不降（ratchet），确保盈利交易不会因
# 追踪止盈过松而反转为亏损。允许回撤空间随浮盈增大而扩大，避免截断趋势中
# 的正常回调。与追踪止盈配合：追踪止盈跟随趋势波动，盈利锁定兜底防止深度回撤。
#
# Tier3 锁定值优化（4% -> 3%）：
# 600550 诊断：02-06 买@15.61，最高收盘 17.77(+13.8%) 触发 Tier3(10%)，
# 锁定 4%=16.23，03-03 收 16.15<16.23 触发盈利锁定卖出，错过后续 +15% 涨幅。
# 降到 3% 后锁定价 16.08，16.15>16.08 不触发，持仓继续抓住趋势延续。
# 影响面极小：8 股 9 笔盈利锁定中仅 600206 10-17 受影响（后续 5 日均 -0.2%，
# 中性），其余 8 笔均在其他 Tier 不受影响。
PROFIT_LOCKIN_TRIGGER1 = 0.05  # V11: 3% → 5% 保本
PROFIT_LOCKIN_TRIGGER2 = 0.09  # V11: 6% → 9% 锁 3%
PROFIT_LOCKIN_FLOOR2 = 0.03
PROFIT_LOCKIN_TRIGGER3 = 0.15  # V11: 10% → 15% 锁 6%
PROFIT_LOCKIN_FLOOR3 = 0.06
PROFIT_LOCKIN_TRIGGER4 = 0.22  # V11: 15% → 22% 锁 10%
PROFIT_LOCKIN_FLOOR4 = 0.10
PROFIT_LOCKIN_TRIGGER5 = 0.30  # V11: 20% → 30% 锁 15%
PROFIT_LOCKIN_FLOOR5 = 0.15

# ── 强趋势盈利锁定豁免 ──────────────────────────────────────────────────────
# ADX>=30 多头强趋势时跳过 Tier1(3%) 和 Tier2(6%) 盈利锁定，从 Tier3(10%) 开始。
# 强趋势中 3-7% 回调是正常洗盘，过早锁定会截断趋势延续。
# 数据驱动：盈利锁定 32 笔交易 avg +1.66%（vs 止盈 avg +23.1%），
# 部分被锁定的交易在强趋势中被过早截断，错失后续主升浪。
PROFIT_LOCKIN_STRONG_TREND_MIN_TIER = 3  # 强趋势从 Tier3 开始锁定

# ── 震荡市快速周转（ADX 识别震荡时缩短持仓）────────────────────────────────
# 600206 等震荡上行股中，趋势型宽追踪（3×ATR）会让利润在来回波动中回吐。
# ADX < 20 = 无方向震荡市（与 dmi_trend.py 阈值一致），此时收紧追踪快速落袋：
# 浮盈 >= 3% 即启用追踪（vs 趋势市 5%），追踪宽度 1.5×ATR（vs 趋势市 3.0）。
# 趋势市（ADX >= 20）保持宽追踪，让利润奔跑。
ADX_RAPID_THRESHOLD = 20  # ADX < 此值视为震荡市
RAPID_PROFIT_TIER = 0.03  # 震荡市浮盈 >= 3% 即启用追踪
RAPID_ATR_TRAIL = 1.5  # 震荡市追踪宽度（比硬止损 2.2 紧，锁住利润）
RAPID_TRAILING_PCT = 4.0  # 震荡市百分比追踪兜底（vs 趋势市 12%）
RAPID_STOP_MULTIPLIER = 1.5  # 震荡市硬止损倍数（比 2.2 紧，快速认错）
RAPID_MAX_HOLD_DAYS = 3
RAPID_MIN_PROFIT_KEEP = 0.02  # 震荡市持仓超时后最低保留浮盈（<2% 主动离场）

# ── 趋势市买入宽限期（趋势中继休整保护）────────────────────────────────────
# 300207 诊断：#5 趋势市买入(ADX=33.9)+多头排列，持仓中 ADX 降到 16.7(震荡市)
# 触发时间止损(holding>=3, profit<2%)卖出+1.36%，但这是"趋势中继休整"——ADX
# 5 日后回升到 30+，主升浪 +38%。趋势市买入的股票给更多时间让趋势恢复，
# 震荡市时间止损 holding 阈值从 3 天提高到 10 天（ADX 回升后自动脱离震荡市）。
TREND_ENTRY_RAPID_HOLD_DAYS = 10  # 趋势市买入(ADX>=20)时震荡市时间止损阈值

# ── 本次优化：MA60 趋势方向过滤 ─────────────────────────────────────────────
MA60_FLAT_LOOKBACK = 20  # MA60 近 20 日方向回看窗口
MA60_DROP_TOLERANCE = 0.005  # MA60 近 20 日跌幅容忍（<=0.5% 视为走平）
# MA60 过滤例外：强趋势 + 涨停级涨幅 + MA60 近20日跌幅 < 3% 时豁免
# 涨停 = 极端多头力量（资金抢筹到涨停），MA60 微跌 = 长期趋势中性（过滤无意义），
# 强趋势（ADX>=30 且 +DI>-DI）= 多头力量持续而非脉冲。三者结合是趋势启动信号。
# 600403 诊断：2025-10-10 涨停(涨幅9.95%,ADX=47,+DI=41.7>>-DI=9.0) + MA60近20日
# 微跌0.62% 被误杀，错过后续 +99.5% 主升浪(持有至10-28止盈)。
# 阈值设计（数据驱动，6 个新增信号对比）：
#   - 涨停(>=9.5%) 排除 600206(3.86%)/002407(6.25%)/000001(1.81%)/000858(3.24%)
#   - MA60跌<3% 排除 600403 03-03(MA60跌6.81%，下跌中继反弹假信号)
MA60_EXCEPTION_GAIN_MIN = 9.5  # 涨停级涨幅下限（%）
MA60_EXCEPTION_DROP_MAX = 0.03  # MA60 近20日跌幅上限（3%）

# ── 中优先级优化：震荡市过滤 ─────────────────────────────────────────────
ADX_PERIOD = 14  # ADX 计算周期
ADX_MIN_THRESHOLD = 20  # ADX 阈值（配合 ADX 下行 + MA20 下行双重判断）
VOLATILITY_MAX_PCT = 8.0  # ATR/价格 > 8% 时不交易（波动过大）

# ── 数据驱动优化：ADX 中等趋势过滤 ─────────────────────────────────────────
# 84 笔交易统计分析：ADX 15-30 区间胜率仅 17%（5 胜 24 负），而 ADX<15 胜率
# 71%、ADX>=40 胜率 50%。中等趋势强度下趋势足以延续但策略误判为反转，
# 是最大的亏损来源。例外：强反转阳线（实体 >= 前日阴线 × 2.0）可突破
# 此过滤，因为强反转意味着买盘足以完全收复前日跌幅。
ADX_MID_RANGE_MIN = 15  # 中等趋势 ADX 下限（低于此值 = 无趋势，反转可靠）
ADX_MID_RANGE_MAX = 30  # 中等趋势 ADX 上限（高于此值 = 强趋势，趋势延续可靠）
# 急跌弱反弹过滤：连续大跌后小阳线反弹，下跌趋势未结束
RECENT_DROP_LOOKBACK = 3  # 回看天数：close[-3] 到 close[-1] 的累计跌幅
RECENT_DROP_MAX = 0.06  # 累计跌幅超过 6% 视为急跌
WEAK_REBOUND_BODY_RATIO = 0.5  # 急跌中当日阳线实体 < 前日阴线实体 × 0.5 视为弱反弹

# 阴线动态止损（非多头排列）
YIN_DROP_ATR_RATIO = 1.2  # 阴线止损 = 当日跌幅 > 1.2×ATR%

# ── 入场质量过滤（超买指标）────────────────────────────────────────────────
# 多股因子分析（400 笔交易，53 只个股）：CCI/MFI/BOLL 超买区入场胜率显著
# 低于基准（42.2%）。这些指标检测「价格已过度扩张」，与影线后收阳的反转
# 逻辑冲突——反转信号在超买区更可能是假突破/追高而非真正的反转起点。
#
# CCI > 100：胜率 36.1%（强趋势 36.5%、非强趋势 35.3%，均低于基准），
#   不豁免强趋势。CCI 衡量价格偏离均值的程度，>100 = 超买扩张。
# MFI > 80：胜率 30.4%，均收益 -1.55%（23 笔），资金流超买的可靠信号。
#   MFI 是带量 RSI，>80 = 资金流入过度，追高风险大。
# BOLL %b > 1.0：非强趋势胜率 26.5%（34 笔，极差），强趋势 43%（接近基准），
#   仅非强趋势时过滤。%b > 1.0 = 收盘价突破布林上轨，非强趋势中突破上轨
#   多为冲高回落假突破；强趋势中突破上轨是趋势延续（正常运行）。
CCI_PERIOD = 14  # CCI 计算周期
CCI_OVERBOUGHT = 100  # CCI 超买阈值（所有趋势中均过滤）
MFI_PERIOD = 14  # MFI 计算周期
MFI_OVERBOUGHT = 80  # MFI 超买阈值（资金流超买，所有趋势中均过滤）
BOLL_PERIOD = 20  # 布林带周期
BOLL_DEVIATION = 2  # 布林带标准差倍数
BOLL_PCTB_OVERBOUGHT = 1.0  # BOLL %b 超买阈值（仅非强趋势时过滤）
BOLL_PCTB_HIGH_ZONE = 0.8  # BOLL %b 高位区间下限（0.8-1.0，非强趋势+影线后收阳专用）
BOLL_PCTB_BULLISH_MAX = 0.8  # 多头排列形成时 BOLL %b 上限（追高保护）

# ── 入场质量过滤（K线实体 + 布林带宽）──────────────────────────────────────
# 多股因子分析（188 笔交易，50 只个股）PF 分析新增高确定性过滤因子：
#
# 阳线实体占比过小（body_ratio < 0.3）：13 笔仅 23.1% 胜率，PF=0.26。
#   body_ratio = |close-open| / (high-low)，即实体占全振幅比例。< 0.3 为
#   小实体阳线（十字星/小阳线），盘中多空争夺未分胜负，反转力度弱。
#   分桶：<=0.3 PF=0.26，0.3-0.5 PF=6.16，0.5-0.7 PF=3.04，>0.7 PF=3.5+。
#
# 布林带宽"死亡区间"（boll_width 20-25%）：20 笔 35% 胜率，PF=0.67。
#   boll_width = (upper-lower)/mid×100。20-25% 是"半收敛半扩张"的不确定
#   区间，方向不明确。注意：带宽 > 25% 胜率反而高（64.7%，趋势明确），
#   带宽 < 20% 也有正期望（收敛待变盘），仅 20-25% 是低效区间。
BODY_RATIO_MIN = 0.3  # 阳线实体/全振幅下限（< 0.3 为小实体，反转力度弱）
BOLL_WIDTH_DEATH_ZONE_LOW = 20.0  # 布林带宽"死亡区间"下限（%）
BOLL_WIDTH_DEATH_ZONE_HIGH = 25.0  # 布林带宽"死亡区间"上限（%）

# ── 入场质量过滤（K线形态 + KDJ 极端超买）──────────────────────────────────
# 多股因子分析（188 笔交易，50 只个股，5 组 wencai 随机样本）：
#
# 下影线过长（lower_shadow > 实体 × 1.0）：18 笔仅 16.7% 胜率（vs 52.4%），
#   均收益 -1.91%。反转策略中长下影线看似支撑强，实为下跌中继的假支撑--
#   盘中抛压将价格砸至低位后小幅回升，收出长下影线，但空头力量仍占主导。
#   分桶：<=0.5 胜率 53.1%，0.5-1.0 胜率 38.1%，1.0-1.5 胜率 0%，>1.5 胜率 21.4%。
#
# KDJ-J > 100：3 笔全部亏损（0% 胜率），均 -1.64%。J 值超过 100 是极端超买，
#   价格已严重偏离均值，反转信号在此区间完全失效。这些交易未被 CCI/MFI/BOLL
#   过滤器捕获（CCI 均 <100、MFI 均 <80），是独立的超买维度。
LOWER_SHADOW_RATIO_MAX = 1.0  # 下影线/实体 比率上限（超过则视为假支撑）
KDJ_PERIOD = 9  # KDJ 计算周期
KDJ_J_OVERBOUGHT = 100  # KDJ-J 极端超买阈值（所有趋势中均过滤）

# ── 假突破过滤（影线后收阳买入专用）────────────────────────────────────────
# 当日阳线长上影线（冲高回落）且收盘价与前两日实体高点相近（偏离 ≤ 2%）时，
# 视为假突破/诱多信号，不买入。市场含义：盘中冲高试图突破前两日高点，但被
# 抛压砸回，收盘价回到前两日高点附近（未有效突破或仅小幅突破），上方阻力
# 大、突破失败。
# 仅应用于「影线后收阳」主买入分支，多头排列形成买入不受影响（趋势确认后
# 长上影线更可能是洗盘而非假突破）。
FAKE_BREAKOUT_SHADOW_RATIO = 1.0  # 阳线长上影线：上影线 > 实体 × 1.0（严格）
FAKE_BREAKOUT_DEVIATION_MAX = 0.02  # 收盘价与前两日 max(收盘,开盘) 偏离 ≤ 2% 视为「相近」

_WAITING = "WAITING"
_HOLDING = "HOLDING"


class ShadowYangStrategy(Strategy):
    """影线后收阳线策略 V11（V10 + 盈利锁定放宽）。

    在 V10（MA20>MA60中期过滤）基础上，放宽盈利锁定阶梯：
    触发门槛上移 50%，锁定比例也相应提高，让盈利单跑更远。
    """

    # 状态机:
    #   WAITING --买入--> HOLDING --止损/止盈--> WAITING

    def init(self) -> None:
        self.ma5 = self.I(MyTT.MA, self.data.close, 5)
        self.ma10 = self.I(MyTT.MA, self.data.close, MA_STOP_PERIOD)
        self.ma20 = self.I(MyTT.MA, self.data.close, MA_LONG_PERIOD)
        self.ma60 = self.I(MyTT.MA, self.data.close, MA_VERY_LONG_PERIOD)
        # ADX 震荡市过滤
        self._pdi, self._mdi, self._adx, self._adxr = self.I(
            MyTT.DMI, self.data.close, self.data.high, self.data.low
        )
        # 入场质量过滤指标（CCI/MFI/BOLL 超买检测）
        self._cci = self.I(MyTT.CCI, self.data.close, self.data.high, self.data.low, CCI_PERIOD)
        self._mfi = self.I(
            MyTT.MFI,
            self.data.close,
            self.data.high,
            self.data.low,
            self.data.vol,
            MFI_PERIOD,
        )
        self._boll_upper, self._boll_mid, self._boll_lower = self.I(
            MyTT.BOLL, self.data.close, BOLL_PERIOD, BOLL_DEVIATION
        )
        # KDJ 极端超买过滤（J > 100 时全部亏损）
        self._kdj_k, self._kdj_d, self._kdj_j = self.I(
            MyTT.KDJ, self.data.close, self.data.high, self.data.low, KDJ_PERIOD, 3, 3
        )
        self._state: str = _WAITING
        self._entry_price: float = 0.0
        self._highest_since_entry: float = 0.0
        self._highest_close_since_entry: float = 0.0  # 收盘价MFE（过滤盘中噪音）
        self._entry_atr: float = 0.0
        self._entry_adx: float = float("nan")  # 买入时 ADX（趋势市买入宽限期判定）
        self._entry_bullish: bool = False  # 买入时多头排列（宽限期仅对趋势确认的股票生效）
        self._holding_days: int = 0
        self._prev_day_yin: bool = False
        self._consecutive_losses: int = 0
        self._cooldown_remaining: int = 0
        # 卖出确认机制（多头排列中首次触发等 1 日确认，避免假跌破）
        self._sell_signal_pending: bool = False

    def next(self) -> None:
        cur_close = self.data.close[0]
        cur_open = self.data.open[0]
        prev_close = self.data.close[-1]

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        # ── 空仓：寻找买入信号 ───────────────────────────────────────────────
        if self._state == _WAITING:
            prev_low = self.data.low[-1]
            self._check_buy(cur_close, cur_open, prev_low)
            return

        # ── 持仓：更新追踪状态 ───────────────────────────────────────────────
        self._holding_days += 1
        cur_high = self.data.high[0]
        if cur_high > self._highest_since_entry:
            self._highest_since_entry = cur_high
        if cur_close > self._highest_close_since_entry:
            self._highest_close_since_entry = cur_close

        i = self._bar_index
        self._check_sell(i, cur_close, cur_open, prev_close)

    # ── 买入逻辑 ─────────────────────────────────────────────────────────────

    def _check_buy(self, cur_close: float, cur_open: float, prev_low: float) -> None:
        i = self._bar_index

        # 冷却期不买入
        if self._cooldown_remaining > 0:
            return

        is_yang = cur_close > cur_open
        is_pullback_reversal_buy = False  # 回调反转买入标志（诊断用）

        # 强趋势判断：ADX>=30 且 +DI>-DI（多头强趋势）时放宽追高保护。
        # 强趋势中的高涨幅/高偏离是趋势延续而非冲顶，不应被追高保护过滤。
        adx_now = self._adx[i]
        pdi_now = self._pdi[i]
        mdi_now = self._mdi[i]
        is_strong_trend = bool(
            adx_now == adx_now
            and pdi_now == pdi_now
            and mdi_now == mdi_now
            and adx_now >= STRONG_TREND_ADX
            and pdi_now > mdi_now
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

        # 数据驱动优化：移除刺穿形态和晨星形态（0% 胜率，24 笔全亏）
        # is_piercing_line: 18 笔 0% 胜率。刺穿形态要求 cur_close < prev_open
        #   （未完全收复前日跌幅），反转力度不足。与看涨吞没/强反转阳线互斥
        #   （后者要求 cur_close >= prev_open），是最弱的反转信号。
        # is_morning_star: 6 笔 0% 胜率。晨星形态中间日实体小（犹豫不决），
        #   反转可靠性低。样本量较小但全部亏损。
        is_ma_condition_met = is_ma5_up or is_bullish_engulfing or is_strong_reversal_yang

        # ── V2 过滤条件 ──────────────────────────────────────────────────────

        # 波动率过滤 - ATR/价格 > 8% 不交易
        atr_now = self._calc_atr(i)
        if cur_close > 0 and atr_now / cur_close * 100 > VOLATILITY_MAX_PCT:
            return

        # V10：中期趋势过滤 - MA20 必须在 MA60 之上
        # 排除中长期下跌通道中的所有抄底信号
        # 例外：强趋势(ADX>=30且+DI>-DI) + 涨停级涨幅时豁免
        # （V2 MA60方向过滤的强趋势例外仍然保留，两者叠加更强）
        ma20_v10 = self.ma20[i]
        ma60_v10 = self.ma60[i]
        if ma20_v10 == ma20_v10 and ma60_v10 == ma60_v10 and ma60_v10 > 0:
            if ma20_v10 < ma60_v10:
                # 强趋势 + 涨停例外
                adx_now_v10 = self._adx[i]
                pdi_now_v10 = self._pdi[i]
                mdi_now_v10 = self._mdi[i]
                gain_v10 = (cur_close - prev_close) / prev_close if prev_close > 0 else 0
                is_strong_v10 = bool(
                    adx_now_v10 == adx_now_v10 and pdi_now_v10 == pdi_now_v10
                    and mdi_now_v10 == mdi_now_v10
                    and adx_now_v10 >= STRONG_TREND_ADX
                    and pdi_now_v10 > mdi_now_v10
                )
                if not (is_strong_v10 and gain_v10 >= MA60_EXCEPTION_GAIN_MIN / 100):
                    return

        # 趋势过滤 - 收盘价须在 MA20 之上
        # 例外：股价 < MA20 但 > MA60 且出现强反转信号（看涨吞没/强反转阳线）时，
        # 允许买入（回调反转买入）。MA60 方向过滤仍会执行，确保中长期趋势向上。
        ma20_now = self.ma20[i]
        is_close_above_ma20 = ma20_now == ma20_now and cur_close > ma20_now
        if not is_close_above_ma20:
            ma60_now = self.ma60[i]
            is_close_above_ma60 = ma60_now == ma60_now and cur_close > ma60_now
            # 回调反转买入仅接受强反转阳线（实体 >= 前日阴线 × 2.0），不接受单纯
            # 看涨吞没。回测诊断：002407 2026-01-15 看涨吞没（非强反转阳线）买入后
            # 亏损 -9.3%，而 600206 2025-09-24 强反转阳线买入后微赚 +1.7%。
            # 看涨吞没不要求实体比例，在 MA20 下方回调场景中假信号率更高。
            is_strong_reversal = is_strong_reversal_yang
            # MA20 方向过滤：MA20 必须走平或上行（避免下跌中继抄底）。
            # 股价跌破 MA20 时若 MA20 仍在下行 = 中期下跌趋势未结束，强反转
            # 信号多为下跌中继反弹假信号；只有 MA20 走平/上行时股价跌破
            # MA20 才是真正的「回调」，反转信号可靠性更高。
            ma20_prev = self.ma20[i - 1] if i > 0 else float("nan")
            is_ma20_not_falling = (
                ma20_prev == ma20_prev and ma20_now == ma20_now and ma20_now >= ma20_prev
            )
            if not (is_close_above_ma60 and is_strong_reversal and is_ma20_not_falling):
                return
            is_pullback_reversal_buy = True

        # 本次优化：MA60 趋势方向过滤 - 近 20 日不可下行（避免长期下跌中继抄底）
        # 2022 熊市回测 6 笔全亏，根因是 MA20 过滤不足以阻挡下跌中继反弹。
        # MA60 是中长期趋势线，下行 = 中长期下跌趋势，不应抄底。
        # 例外：强趋势 + 涨停级涨幅 + MA60 近20日跌幅 < 3% 时豁免。
        # 涨停 = 资金最强烈看多表达，MA60 微跌 = 趋势中性（过滤无意义），
        # 强趋势 = 多头力量持续而非脉冲。三者结合是趋势启动信号。
        # 数据驱动阈值设计（6 个新增信号对比）：
        #   - 涨停(>=9.5%) 排除 600206(3.86%)/002407(6.25%)/000001(1.81%)/000858(3.24%)
        #     非涨停的强反转阳线力度不足以确认趋势启动，全部亏损
        #   - MA60跌<3% 排除 600403 03-03（涨停但 MA60跌6.81%，下跌中继反弹假信号）
        # 600403 诊断：2025-10-10 涨停(涨幅9.95%,ADX=47,+DI=41.7>>-DI=9.0) + MA60近20日
        # 微跌0.62% 被误杀，错过后续 +99.5% 主升浪(持有至10-28止盈)。
        if i >= MA60_FLAT_LOOKBACK:
            ma60_now = self.ma60[i]
            ma60_ago = self.ma60[i - MA60_FLAT_LOOKBACK]
            if ma60_now == ma60_now and ma60_ago == ma60_ago and ma60_ago > 0:
                ma60_drop = (ma60_ago - ma60_now) / ma60_ago
                if ma60_drop > MA60_DROP_TOLERANCE:
                    gain_pct = (cur_close - prev_close) / prev_close if prev_close > 0 else 0
                    is_limit_up = gain_pct >= MA60_EXCEPTION_GAIN_MIN / 100
                    if not (
                        is_strong_trend and is_limit_up and ma60_drop < MA60_EXCEPTION_DROP_MAX
                    ):
                        return

        # ADX 震荡市过滤 - 仅在趋势未确认时生效
        # 价格已站上 MA20 = 趋势确认，ADX 低位只是新趋势起步，不应过滤
        # 只有 MA20 走平/下行 + ADX<20 且下行 才过滤（真正的无方向震荡）
        # 注：adx_now 已在上方强趋势判断中读取
        if i >= 5 and adx_now == adx_now:
            adx_5ago = self._adx[i - 5]
            ma20_5ago = self.ma20[i - 5]
            if (
                adx_5ago == adx_5ago
                and ma20_5ago == ma20_5ago
                and adx_now < ADX_MIN_THRESHOLD
                and adx_now < adx_5ago
                and ma20_now <= ma20_5ago
            ):  # MA20 也在下行才是真震荡
                return

        # ADX 中等趋势过滤：15 <= ADX < 30 时反转信号可靠性低
        # 数据驱动（84 笔交易）：ADX 15-30 区间胜率仅 17%（5 胜 24 负），
        # 是最大亏损来源。中等趋势强度下趋势足以延续但策略误判为反转。
        # 例外：强反转阳线（实体 >= 前日阴线 × 2.0）可突破此过滤，
        # 因为强反转意味着买盘完全收复前日跌幅，即使在中等趋势中也可靠。
        if adx_now == adx_now and ADX_MID_RANGE_MIN <= adx_now < ADX_MID_RANGE_MAX:
            if not is_strong_reversal_yang:
                return

        # 追高保护 - 偏离 MA20（强趋势时放宽到 35%，避免主升浪被过滤）
        dev_max = STRONG_TREND_DEV_MAX if is_strong_trend else MA20_DEVIATION_MAX
        if ma20_now > 0:
            deviation = (cur_close - ma20_now) / ma20_now
            if deviation > dev_max:
                return

        # 追高保护 - 近 5 日涨幅（强趋势时放宽到 30%）
        surge5_max = STRONG_TREND_SURGE5_MAX if is_strong_trend else RECENT_SURGE_MAX
        if i >= RECENT_SURGE_DAYS:
            close_5ago = self.data.close[-RECENT_SURGE_DAYS]
            if close_5ago > 0:
                recent_gain = (cur_close - close_5ago) / close_5ago
                if recent_gain > surge5_max:
                    return

        # 追高保护（短期）- 近 3 日涨幅
        # 强趋势或多头排列时放宽到 18%：
        #   - 强趋势：起飞日涨幅大但趋势可持续
        #   - 多头排列：趋势已确认（MA5>MA20>MA60 且 MA10>MA20>MA60），
        #     V 型反弹涨幅非冲顶追高（如急跌低点后的反弹）
        is_bullish = self._is_ma_bullish(i)
        surge3_max = STRONG_TREND_SURGE3_MAX if (is_strong_trend or is_bullish) else SHORT_SURGE_MAX
        if i >= SHORT_SURGE_DAYS:
            close_3ago = self.data.close[-SHORT_SURGE_DAYS]
            if close_3ago > 0:
                short_gain = (cur_close - close_3ago) / close_3ago
                if short_gain > surge3_max:
                    return

        # 成交量确认 - 当日量 >= 5 日均量
        if i >= 5:
            vol_now = self.data.vol[0]
            vol_ma5 = sum(self.data.vol[-j] for j in range(1, 6)) / 5.0
            if vol_ma5 > 0 and vol_now < vol_ma5 * VOLUME_RATIO_MIN:
                return

        # 急跌弱反弹过滤：前 2 日累计跌幅过大 + 当日反弹力度不足，不买入
        if i >= RECENT_DROP_LOOKBACK:
            close_3ago = self.data.close[-3]
            if close_3ago > 0:
                recent_drop = (prev_close - close_3ago) / close_3ago
                if recent_drop < -RECENT_DROP_MAX:
                    cur_body = cur_close - cur_open
                    prev_body = prev_open - prev_close
                    if prev_body > 0 and cur_body < prev_body * WEAK_REBOUND_BODY_RATIO:
                        return

        # ── 入场质量过滤（超买指标）──────────────────────────────────────────
        # 多股因子分析（400 笔交易，53 只个股）：CCI/MFI/BOLL 超买区入场胜率
        # 显著低于基准（42.2%）。反转策略在超买区信号可靠性低。
        # CCI > 100：胜率 36.1%（强/弱趋势均低，不豁免）
        # MFI > 80：胜率 30.4%，均收益 -1.55%（资金流超买，不豁免）
        # BOLL %b > 1.0：非强趋势胜率 26.5%（强趋势 43% 接近基准，仅非强趋势过滤）
        # BOLL %b 0.8-1.0：非强趋势+影线后收阳 20%胜率，仅影线后收阳路径过滤
        #
        # 超买过滤应用于所有买入路径（含多头排列形成）。多股回测验证：
        # 10 只个股总收益 +11.17%（vs 基线 -146.66%），"多头排列形成"在超买区
        # 入场同样多为亏损，601991 2026-05-07 +49.6% 为孤例不可作为策略依据。
        cci_now = self._cci[i]
        if cci_now == cci_now and cci_now > CCI_OVERBOUGHT:
            return

        mfi_now = self._mfi[i]
        if mfi_now == mfi_now and mfi_now > MFI_OVERBOUGHT:
            return

        # BOLL %b 高位区间标志（0.8-1.0 非强趋势，影线后收阳专用过滤）
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

        # 布林带宽"死亡区间"过滤（boll_width 20-25%）：20 笔 35% 胜率，PF=0.67
        # 带宽 20-25% 是"半收敛半扩张"的不确定区间，方向不明确。
        # 带宽 > 25% 胜率反而高（64.7%，趋势明确），< 20% 也有正期望（收敛待变盘）。
        boll_mid = self._boll_mid[i]
        if boll_mid == boll_mid and boll_mid > 0:
            boll_upper_w = self._boll_upper[i]
            boll_lower_w = self._boll_lower[i]
            if boll_upper_w == boll_upper_w and boll_lower_w == boll_lower_w:
                boll_width_pct = (boll_upper_w - boll_lower_w) / boll_mid * 100
                if BOLL_WIDTH_DEATH_ZONE_LOW <= boll_width_pct <= BOLL_WIDTH_DEATH_ZONE_HIGH:
                    return

        # ── K 线形态 + KDJ 极端超买过滤 ──────────────────────────────────
        # 下影线过长：下影线 > 实体 × 1.0 时为假支撑（18 笔 16.7% 胜率）
        cur_low = self.data.low[0]
        lower_shadow = min(cur_open, cur_close) - cur_low
        cur_body_abs = abs(cur_close - cur_open)
        if cur_body_abs > 0 and lower_shadow > cur_body_abs * LOWER_SHADOW_RATIO_MAX:
            return

        # KDJ-J > 100：极端超买，3 笔全部亏损（0% 胜率）
        kdj_j_now = self._kdj_j[i]
        if kdj_j_now == kdj_j_now and kdj_j_now > KDJ_J_OVERBOUGHT:
            return

        # 阳线实体占比过小（body_ratio < 0.3）：13 笔仅 23.1% 胜率，PF=0.26
        # 小实体阳线（十字星/小阳线）反转力度弱，盘中多空争夺未分胜负
        cur_high = self.data.high[0]
        full_range = cur_high - cur_low
        if full_range > 0 and cur_body_abs / full_range < BODY_RATIO_MIN:
            return

        # ── 原始条件 ─────────────────────────────────────────────────────────

        # 空头强趋势过滤：前一日 ADX>=30 且 -DI >= +DI*2 时不买入
        # 600539 诊断：2025-10-15 买入日 +DI 被阳线推高(11.9->16.9)使 -DI/+DI
        # 从 2.88(前日) 降到 1.88(当日)，用前日指标可避免此干扰
        # 002407 2026-02-11：前日 -DI/+DI=1.68 < 2.0，是有效信号，不应过滤
        # 例外：回调反转买入不受此过滤（逆势信号本身就是逆势的，空头强趋势
        # 中的强反转阳线反而可能是恐慌性下跌后的反转机会）
        pdi_prev = self._pdi[i - 1] if i > 0 else float("nan")
        mdi_prev = self._mdi[i - 1] if i > 0 else float("nan")
        is_bearish_strong_trend = bool(
            adx_now == adx_now
            and pdi_prev == pdi_prev
            and mdi_prev == mdi_prev
            and adx_now >= STRONG_TREND_ADX
            and mdi_prev >= pdi_prev * BEAR_STRONG_TREND_DI_RATIO
        )

        # 假突破过滤（仅影线后收阳买入）：当日阳线长上影线（冲高回落）且
        # 收盘价与前两日实体高点相近（偏离 ≤ 2%）时，视为假突破/诱多，不买入。
        # 多头排列形成买入不受此过滤（趋势确认后长上影线多为洗盘）。
        is_fake_breakout = False
        if i >= 2 and is_yang:
            cur_high = self.data.high[0]
            cur_body = cur_close - cur_open
            if cur_body > 0:
                upper_shadow = cur_high - cur_close
                if upper_shadow > cur_body * FAKE_BREAKOUT_SHADOW_RATIO:
                    prev2_open = self.data.open[-2]
                    prev2_close = self.data.close[-2]
                    prev_two_max = max(prev_open, prev_close, prev2_open, prev2_close)
                    if prev_two_max > 0:
                        # 收盘价与前两日高点的偏离幅度（绝对值）
                        deviation = abs(cur_close - prev_two_max) / prev_two_max
                        # 假突破：收盘价在前两日高点 ±2% 内（未有效突破或仅小幅突破）
                        is_fake_breakout = deviation <= FAKE_BREAKOUT_DEVIATION_MAX

        if (
            is_yang
            and is_ma_condition_met
            and is_close_above_prev_low
            and is_prev_yin
            and is_prev_no_long_upper_shadow
            and (is_pullback_reversal_buy or not is_bearish_strong_trend)
            and not is_fake_breakout
            and not is_boll_high_non_strong
        ):
            atr = self._calc_atr(i)
            buy_reason = "回调反转买入" if is_pullback_reversal_buy else "影线后收阳"
            self.buy(size=0, price=cur_close, reason=buy_reason)
            self._state = _HOLDING
            self._entry_price = cur_close
            self._highest_since_entry = cur_close
            self._highest_close_since_entry = cur_close
            self._entry_atr = atr
            self._entry_adx = self._adx[i]
            self._entry_bullish = self._is_ma_bullish(i)
            self._holding_days = 0
            self._prev_day_yin = False
            self._sell_signal_pending = False
        elif is_yang:
            # 多头排列首次形成买入（不依赖"前日阴线"形态，捕捉主升浪起点）
            # 600539 诊断：主升浪期间大量连续阳线，无"前日阴线"形态，策略完全错过。
            # 多头排列首次形成 = 今天满足多头排列且昨天不满足，是主升浪起点信号。
            # 回测诊断：该信号胜率 57.1%，平均收益 6.96%（正期望）。
            # 上方过滤条件（MA60方向、追高保护、成交量、急跌弱反弹）已全部执行，
            # 多头排列中 MA20 必然上行故 ADX 震荡市过滤不会误杀。
            is_bullish_now = self._is_ma_bullish(i)
            is_bullish_prev = self._is_ma_bullish(i - 1) if i > 0 else False
            if is_bullish_now and not is_bullish_prev:
                # 多头排列形成 + BOLL%b > 0.8（非强趋势）：7 笔仅 28.6% 胜率，PF=0.28
                # 多头排列刚形成时价格已在布林上轨附近，追高风险大。
                # 仅非强趋势时过滤（强趋势中 BOLL%b 高位是趋势延续）。
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
                self.buy(size=0, price=cur_close, reason="多头排列形成")
                self._state = _HOLDING
                self._entry_price = cur_close
                self._highest_since_entry = cur_close
                self._highest_close_since_entry = cur_close
                self._entry_atr = atr
                self._entry_adx = self._adx[i]
                self._entry_bullish = self._is_ma_bullish(i)
                self._holding_days = 0
                self._prev_day_yin = False
                self._sell_signal_pending = False

    # ── 卖出逻辑 ─────────────────────────────────────────────────────────────

    def _check_sell(self, i: int, cur_close: float, cur_open: float, prev_close: float) -> None:
        cur_yin = cur_close < cur_open
        atr = self._entry_atr if self._entry_atr > 0 else self._calc_atr(i)

        # A. ATR 硬止损（立即执行，无需确认）—— 底线保护
        # 震荡市判定（ADX < 阈值）：收紧止损 + 时间止损，快速周转不恋战
        adx_now = self._adx[i]
        is_ranging = adx_now == adx_now and adx_now < ADX_RAPID_THRESHOLD

        # 买入时强趋势（趋势中继休整保护）
        # 603127 诊断：大牛股中 ADX 频繁在 20-50 之间波动，每次 ADX 跌破 30
        # 都会导致强趋势豁免失效，恰好在趋势中继休整时触发过早卖出。
        # 买入时 ADX>=30 的持仓，ADX 回调更可能是中继休整而非趋势反转，
        # 应保持趋势跟踪的退出策略（放宽盈利锁定/破位卖出/最大亏损/阴线止损/时间止损）。
        # 不要求多头排列：603127 #1 买入时 ADX=41.4 强趋势但 MA5<MA10（均线修复中），
        # 多头排列要求会漏掉这类强趋势早期买入点。
        is_entry_strong_trend = bool(
            self._entry_adx == self._entry_adx
            and self._entry_adx >= STRONG_TREND_ADX
        )

        # 震荡市收紧硬止损（无方向市场中宽止损无意义，快速认错）
        stop_mult = RAPID_STOP_MULTIPLIER if is_ranging else ATR_STOP_MULTIPLIER
        hard_stop = self._entry_price - stop_mult * atr
        if cur_close <= hard_stop:
            self._exit_position(cur_close, is_loss=True)
            return

        # B. 最大亏损保护（无论任何条件，亏损超过 MAX_LOSS_PCT 强制止损）
        # 不豁免：最大亏损保护是最后防线，趋势反转时必须及时认错。
        # 603127 #2 实测：豁免后持仓从 -7% 扩大到 -18.52%，验证不应放宽。
        loss_pct = (self._entry_price - cur_close) / self._entry_price * 100
        if loss_pct >= MAX_LOSS_PCT:
            self._exit_position(cur_close, is_loss=True)
            return

        profit_pct = (cur_close - self._entry_price) / self._entry_price

        # C. 盈利锁定阶梯（ratchet）：基于最高浮盈锁定利润下限，防止盈转亏
        # MAE/MFE 分析：17 笔交易曾盈利 3-21% 但最终亏损，根因是浮盈 0-5% 区间
        # 无追踪保护 + 5-15% 追踪过松。盈利锁定以最高浮盈为基准，一旦达到触发
        # 阈值即将止损锁定在对应水平（只升不降），防止利润全部回吐甚至转亏。
        # 在追踪止盈之前检查：追踪止盈跟随趋势波动，盈利锁定兜底防止深度回撤。
        # 买入时强趋势豁免：跳过 Tier3，从 Tier4 开始锁定，避免趋势中继休整
        # 期间过早锁定而截断趋势延续。
        profit_floor = self._calc_profit_floor(is_entry_strong_trend)
        if profit_floor > 0 and cur_close <= profit_floor:
            self._exit_position(cur_close, is_loss=profit_pct < 0, reason="盈利锁定")
            return

        # D. 分阶段追踪止盈（浮盈越大追踪越宽，让趋势单跑起来）
        # 回测显示持仓 20 天+胜率 100%，原 7% 追踪太紧把起飞前单子震出。
        # 改为：浮盈 <5% 不追踪（只靠硬止损给足空间），5-15% 宽追踪，>15% 收紧。
        if self._highest_since_entry > self._entry_price and profit_pct > 0:
            trail_stop = self._calc_trailing_stop(atr, profit_pct, adx_now)
            if trail_stop is not None and cur_close <= trail_stop:
                self._exit_position(cur_close, is_loss=profit_pct < 0)
                return

        # D/E. 均线多头排列逻辑（提前计算，G 部分需要使用）
        is_bullish = self._is_ma_bullish(i)

        # G. 震荡市时间止损：持仓超 N 天且浮盈不足，主动离场（不恋战）
        # 震荡市价格来回波动无方向，持仓过久不赚钱说明没有趋势，应换标的。
        # 例外：多头排列中不触发（多头排列说明趋势已形成，ADX 低只是趋势
        # 起步阶段，不应因 ADX<20 就时间止损清出）
        # 600539 诊断：#2 多头排列+ADX=12.8 被震荡市时间止损清出，卖后涨 52%
        #
        # 趋势市买入宽限期：趋势市买入(ADX>=20)且买入时多头排列的股票给更多时间
        # 让趋势恢复。300207 诊断：#5 多头排列+ADX=33.9 买入后 ADX 降到 16.7，
        # 原阈值 holding>=3 误杀趋势中继休整（卖后主升浪+38%）。
        # 多头排列=趋势已确认，ADX 暂降是休整而非反转；非多头排列=趋势未确认，
        # ADX 下降更可能反转（002407 #5 多头排列=False 延迟止损致 -2.9% vs +0.7%）。
        if (
            self._entry_adx == self._entry_adx
            and self._entry_adx >= ADX_RAPID_THRESHOLD
            and self._entry_bullish
        ):
            hold_threshold = TREND_ENTRY_RAPID_HOLD_DAYS
        else:
            hold_threshold = RAPID_MAX_HOLD_DAYS
        if (
            is_ranging
            and not is_bullish
            and not (is_entry_strong_trend and profit_pct > 0)
            and self._holding_days >= hold_threshold
            and profit_pct < RAPID_MIN_PROFIT_KEEP
        ):
            self._exit_position(cur_close, is_loss=profit_pct < 0, reason="震荡市时间止损")
            return

        if is_bullish:
            ma20_now = self.ma20[i]

            # 浮盈 > 10% 放宽止损到 MA20
            if profit_pct >= PROFIT_RELAX_THRESHOLD:
                if cur_close < ma20_now:
                    # 亏损中破位卖出标记止损，触发连续亏损冷却保护
                    self._exit_position(cur_close, is_loss=profit_pct < 0)
                    return
            else:
                # 多头排列中的动态阴线止损（与 F 分支一致）
                # 600539 诊断：2025-08-07 高开低走大阴线（跌 17.7%）在多头排列中
                # 无止损保护，should_sell=False（is_converged 且 max(O,C)>=MA10），
                # 持仓到 08-08 被 ATR 硬止损（多亏 8064）
                if cur_yin:
                    atr_pct_d = atr / cur_open * 100 if cur_open > 0 else 3.0
                    yin_drop_pct_d = (cur_open - cur_close) / cur_open * 100 if cur_open > 0 else 0
                    pdi_now_d = self._pdi[i]
                    mdi_now_d = self._mdi[i]
                    is_strong_trend_relax_d = bool(
                        adx_now == adx_now
                        and pdi_now_d == pdi_now_d
                        and mdi_now_d == mdi_now_d
                        and adx_now >= STRONG_TREND_ADX
                        and pdi_now_d > mdi_now_d
                        and cur_close > ma20_now
                        and profit_pct > 0
                    )
                    if is_strong_trend_relax_d:
                        dynamic_threshold_d = max(
                            STRONG_TREND_YIN_DROP_RATIO * atr_pct_d,
                            STRONG_TREND_YIN_MIN_PCT,
                        )
                    else:
                        # 多头排列中阴线止损比非多头排列放宽
                        # 600539 诊断：#2 多头排列中 7.2% 阴线被原 1.2×ATR%(6.0%) 清出
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

                # 买入时强趋势+多头排列豁免：趋势中继休整中的回调不应触发破位卖出
                # 603127 诊断：#2 买入时 ADX=42.5+多头排列，3-25 开盘<MA10+阴线
                # 触发破位卖出(-4.8%)，但 ADX=50.6(强趋势)只是正常回调，错过+154.6%
                # 由 ATR 硬止损和最大亏损保护兜底
                # 仅在浮盈时豁免：亏损中的破位卖出是有效的止损信号。
                # 920725 #2 诊断：亏损中破位卖出被跳过，等到 -8% 最大亏损保护才止损，
                # 亏损从 -3.48% 扩大到 -8.81%
                if is_entry_strong_trend and profit_pct > 0:
                    should_sell = False

                if should_sell:
                    # 卖出确认 - 多头排列中首次触发等 1 日确认（避免假跌破）
                    if not self._sell_signal_pending:
                        self._sell_signal_pending = True
                        self._prev_day_yin = cur_yin
                        return
                    else:
                        self._sell_signal_pending = False
                        # 亏损中破位卖出标记止损，触发连续亏损冷却保护
                        self._exit_position(cur_close, is_loss=profit_pct < 0)
                        return
                else:
                    # should_sell=False 时清除 pending（避免延迟触发）
                    # 600539 诊断：#4 05-14 首次 should_sell=True 设 pending，
                    # 05-15~05-20 should_sell=False 但 pending 未清除，
                    # 05-21 should_sell=True 时直接卖出（延迟 7 天触发）
                    self._sell_signal_pending = False
        else:
            # F. 非多头排列：以 ATR 硬止损为底线，辅以低开/动态阴线止损
            # 本次优化：移除「第一日下跌减半」（短持仓亏损主因），改为持有
            # 等待转多头排列或触及硬止损，让单子有时间发展。
            # 强趋势判断（与 _check_buy 一致）：ADX>=30 且 +DI>-DI
            # 提前计算：低开止损需用强趋势例外
            pdi_now = self._pdi[i]
            mdi_now = self._mdi[i]
            is_strong_trend = bool(
                adx_now == adx_now
                and pdi_now == pdi_now
                and mdi_now == mdi_now
                and adx_now >= STRONG_TREND_ADX
                and pdi_now > mdi_now
            )
            # 例外：强多头趋势中低开更可能是主升浪洗盘而非趋势反转，不因低开
            # 止损清仓，改由 ATR 硬止损和动态阴线止损保护。
            # 600403 诊断：2025-10-13 强多头(ADX=58.7,+DI=46.6>>-DI=7.6)低开4.18%
            # 被低开止损清出，错过后续 +26.4% 主升浪(10-15 涨至5.45)。
            is_low_open = self._is_low_open(cur_open, prev_close)
            if is_low_open and not is_strong_trend:
                self._exit_position(cur_open, is_loss=True)
                return
            # 强趋势阴线止损放宽仅在价格站上 MA20 且浮盈为正时生效
            # （非多头排列但价格在 MA20 之上 + 盈利中 = 均线修复中的回调，
            #  阴线更可能是主升浪洗盘；亏损中的阴线更可能是趋势反转。
            #  000001 诊断：银行股 ADX 44-79 且 close>MA20，但卖出时全部
            #  亏损，放宽止损后持有更久反而加重亏损）
            ma20_now_f = self.ma20[i]
            is_strong_trend_relax = bool(
                is_strong_trend
                and ma20_now_f == ma20_now_f
                and cur_close > ma20_now_f
                and profit_pct > 0
            )

            # 动态阴线止损（跌幅 > ATR%×倍数）或连续 2 日阴线
            # 强趋势 + 价格在 MA20 之上时放宽阈值，避免主升浪回调被震出
            # 002156 诊断：ADX=86.1 时 4.46% 阴线回调被原 1.2×ATR%(3.82%)
            # 清出，错过后续 +39.8% 主升浪。
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
                # 连续 2 日阴线止损 - 强趋势放宽时不禁用（主升浪中连续阴线
                # 洗盘是正常现象，只靠动态阴线止损阈值和追踪止盈保护）
                if self._prev_day_yin and not is_strong_trend_relax:
                    self._exit_position(cur_close, is_loss=True)
                    return

        # 重置卖出确认状态（未触发卖出时清除 pending）
        if not is_bullish:
            self._sell_signal_pending = False
        self._prev_day_yin = cur_yin

    def _calc_profit_floor(
        self, is_entry_strong_trend: bool = False
    ) -> float:
        """计算盈利锁定阶梯止损价（ratchet mechanism）。

        以持仓期间最高收盘浮盈（_highest_close_since_entry）为基准，一旦收盘
        达到触发阈值即将止损锁定在对应水平，只升不降。使用收盘价而非最高价，
        过滤盘中尖峰噪音，避免因日内冲高回落而过早触发锁定。

        阶梯设计（允许回撤空间随浮盈增大而扩大，避免截断趋势中的正常回调）：
          3% 收盘浮盈 -> 保本（0%），允许 3% 回撤
          6% 收盘浮盈 -> 锁定 2%，允许 4% 回撤
          10% 收盘浮盈 -> 锁定 3%，允许 7% 回撤
          15% 收盘浮盈 -> 锁定 7%，允许 8% 回撤
          20% 收盘浮盈 -> 锁定 10%，允许 10% 回撤

        买入时强趋势豁免（is_entry_strong_trend）：
          豁免 Tier3(10%) 的 3% 锁定。趋势中继休整期间 ADX 可能暂降到 30 以下，
          但趋势仍将延续，不应在 10-15% 浮盈区间过早锁定 3%。
          603127 #1：Tier3 豁免后持仓延长到 11-15，由 Tier4(15%) 触发卖出 +4.96%
          600744 #3：05-09 达到 Tier4(18%)，Tier4 底线 5.20 在 05-15 触发 +1.65%

          注意：不豁免 Tier1/Tier2。低浮盈(3-6%)时趋势尚未充分确认，保本/小利
          锁定仍有价值。603127 #2 实测：买入时 ADX=42.5 但最高浮盈仅 4.12%，
          豁免 Tier1 后持仓从 -3.75% 扩大到 -7.00%。

        Args:
            is_entry_strong_trend: 买入时是否为强趋势+多头排列（趋势中继休整保护）

        Returns:
            盈利锁定止损价；未达触发阈值时返回 0.0（不启用）
        """
        if self._entry_price <= 0 or self._highest_close_since_entry <= self._entry_price:
            return 0.0
        max_profit = (self._highest_close_since_entry - self._entry_price) / self._entry_price
        if max_profit >= PROFIT_LOCKIN_TRIGGER5:
            return self._entry_price * (1 + PROFIT_LOCKIN_FLOOR5)
        if max_profit >= PROFIT_LOCKIN_TRIGGER4:
            return self._entry_price * (1 + PROFIT_LOCKIN_FLOOR4)
        if max_profit >= PROFIT_LOCKIN_TRIGGER3:
            # 买入时强趋势：豁免 Tier3(3%) 锁定。趋势中继休整期间 ADX 可能暂降，
            # 但趋势仍将延续，不应在 10-15% 浮盈区间过早锁定 3%。
            # 603127 #1：Tier3 豁免后持仓延长到 11-15，由 Tier4(15%) 触发 +4.96%
            # 600744 #3：05-09 达到 Tier4(18%)，Tier4 底线 5.20 在 05-15 触发 +1.65%
            if is_entry_strong_trend:
                return 0.0
            return self._entry_price * (1 + PROFIT_LOCKIN_FLOOR3)
        # 不豁免 Tier1/Tier2：低浮盈(3-6%)时趋势尚未充分确认，保本/小利锁定
        # 仍有价值。603127 #2 实测：买入时 ADX=42.5 但最高浮盈仅 4.12%，
        # 豁免 Tier1 后持仓从 -3.75% 扩大到 -7.00%。
        if max_profit >= PROFIT_LOCKIN_TRIGGER2:
            return self._entry_price * (1 + PROFIT_LOCKIN_FLOOR2)
        if max_profit >= PROFIT_LOCKIN_TRIGGER1:
            return self._entry_price  # 保本
        return 0.0  # 未达触发阈值，不启用

    def _calc_trailing_stop(self, atr: float, profit_pct: float, adx_now: float) -> float | None:
        """计算分阶段追踪止盈止损价。

        震荡市（ADX < ADX_RAPID_THRESHOLD）收紧追踪快速落袋；
        趋势市浮盈越大追踪越宽，让趋势单跑起来。

        Args:
            atr: 入场时 ATR（用于稳定追踪宽度）
            profit_pct: 当前浮盈比例
            adx_now: 当前 ADX 值（NaN 时按趋势市处理）

        Returns:
            追踪止损价；浮盈不足启用阈值时返回 None（不启用追踪）
        """
        # 震荡市快速周转：ADX < 阈值时收紧追踪，避免利润在来回波动中回吐
        is_ranging = adx_now == adx_now and adx_now < ADX_RAPID_THRESHOLD
        if is_ranging:
            if profit_pct < RAPID_PROFIT_TIER:
                # 震荡市浮盈 < 3%：不追踪，只靠硬止损
                return None
            atr_mult = RAPID_ATR_TRAIL
            trailing_pct = RAPID_TRAILING_PCT
        elif profit_pct < PROFIT_TIER2:
            # 趋势市：浮盈 < 5% 不追踪，只靠 ATR 硬止损（给足起飞空间）
            return None
        elif profit_pct >= PROFIT_TIER4:
            # 趋势市：浮盈 > 25% 超大幅盈利，放宽追踪让超大趋势充分奔跑
            atr_mult = ATR_TRAIL_MEGA
            trailing_pct = TRAILING_STOP_PCT_MAX
        elif profit_pct >= PROFIT_TIER3:
            # 趋势市：浮盈 > 15% 收紧追踪，锁定大部分利润
            atr_mult = ATR_TRAIL_TIGHT
            trailing_pct = TRAILING_STOP_PCT_MAX
        else:
            # 趋势市：浮盈 5%-15% 宽追踪，让趋势波动不被震出
            atr_mult = ATR_TRAIL_WIDE
            trailing_pct = TRAILING_STOP_PCT_MAX

        atr_trail = self._highest_since_entry - atr_mult * atr
        # 百分比追踪兜底（取较宽者，避免 ATR 过小时追踪过紧）
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

    # ── ATR 计算 ─────────────────────────────────────────────────────────────

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

    # ── 内部方法 ─────────────────────────────────────────────────────────────

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
        prev_open: float,
        prev_high: float,
        prev_close: float,
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
    def _is_piercing_line(
        cur_open: float, cur_close: float, prev_open: float, prev_close: float, prev_low: float
    ) -> bool:
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
    def _is_morning_star(
        cur_open: float,
        cur_close: float,
        prev_open: float,
        prev_close: float,
        prev2_open: float,
        prev2_close: float,
    ) -> bool:
        values = (cur_open, cur_close, prev_open, prev_close, prev2_open, prev2_close)
        if not all(v == v for v in values):
            return False
        if prev2_close >= prev2_open:
            return False
        prev2_body = prev2_open - prev2_close
        prev_body = abs(prev_open - prev_close)
        if prev_body >= prev2_body * STAR_BODY_RATIO:
            return False
        if max(prev_open, prev_close) >= prev2_close:
            return False
        if cur_close <= cur_open:
            return False
        midpoint = (prev2_open + prev2_close) / 2
        return cur_close > midpoint

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
