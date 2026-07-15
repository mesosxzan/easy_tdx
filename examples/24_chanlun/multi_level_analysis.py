"""演示：多级别联立缠论分析。

使用 MultiLevelAnalyser 同时分析日线 + 30 分钟线 + 5 分钟线，
查看高级别笔在低级别中的走势结构，辅助判断买卖点的有效性。

缠论多级别联立分析是核心实战方法：
    - 高级别（日线）判断大趋势方向和买卖点
    - 低级别（30min/5min）精确定位买卖点和背驰

MultiLevelAnalyser API:
    add_level(name, analyser)            -- 添加一个分析级别
    process(level, df)                  -- 处理指定级别的 K 线数据
    get_result(level)                   -- 获取指定级别的分析结果
    results()                           -- 获取所有级别的分析结果
    query_low_level_qs(high, low, bi)   -- 查询高级别笔在低级别中的走势

query_low_level_qs 返回:
    bi_count             -- 低级别笔数
    zs_count             -- 低级别中枢数
    has_trend            -- 是否形成趋势（>=2 个中枢）
    has_consolidation    -- 是否形成盘整（>=1 个中枢）
    trend_direction      -- 趋势方向（"up"/"down"/None）
    bi_overlap           -- 相邻笔是否有重叠（盘整特征）
    divergence_possible  -- 是否具备背驰条件

参数:
    market  -- 市场代码（Market.SH=1, Market.SZ=0）
    code    -- 股票代码
    adjust  -- 复权方式（默认 QFQ 前复权）
"""

from easy_tdx import Adjust, MacClient, Market, Period
from easy_tdx.chanlun import ChanlunAnalyser, ChanlunConfig
from easy_tdx.chanlun.multi_level import MultiLevelAnalyser

with MacClient.from_best_host() as c:
    # --- 获取多级别 K 线数据 ---
    print("=== 获取多级别 K 线数据 ===")
    df_daily = c.get_stock_kline(Market.SH, "600519", Period.DAILY, count=300, adjust=Adjust.QFQ)
    df_30min = c.get_stock_kline(Market.SH, "600519", Period.MIN_30, count=400, adjust=Adjust.QFQ)
    df_5min = c.get_stock_kline(Market.SH, "600519", Period.MIN_5, count=500, adjust=Adjust.QFQ)
    print(f"日线: {len(df_daily)} 根, 30分钟: {len(df_30min)} 根, 5分钟: {len(df_5min)} 根")

    # --- 初始化多级别分析器 ---
    mla = MultiLevelAnalyser()
    mla.add_level("daily", ChanlunAnalyser("SH600519", "daily"))
    mla.add_level("30min", ChanlunAnalyser("SH600519", "30min"))
    mla.add_level("5min", ChanlunAnalyser("SH600519", "5min"))

    # --- 处理各级别数据 ---
    print("\n=== 各级别缠论分析结果 ===")
    for level_name, df in [("daily", df_daily), ("30min", df_30min), ("5min", df_5min)]:
        result = mla.process(level_name, df)
        print(
            f"  {level_name:6s}: {len(result.bis):3d} 笔, "
            f"{len(result.zss):3d} 中枢, "
            f"{len(result.xds):3d} 线段, "
            f"{len(result.mmds):3d} 买卖点"
        )

    # --- 查看日线最后一笔在 30 分钟中的走势 ---
    daily_result = mla.get_result("daily")
    assert daily_result is not None
    if daily_result.bis:
        last_bi = daily_result.bis[-1]
        print("\n=== 日线最后一笔在 30 分钟中的走势 ===")
        direction = "↑" if last_bi.direction.value == "up" else "↓"
        print(
            f"日线笔#{last_bi.index} {direction} "
            f"{last_bi.start.k.date:%Y-%m-%d} → {last_bi.end.k.date:%Y-%m-%d} "
            f"高={last_bi.high:.2f} 低={last_bi.low:.2f}"
        )

        qs_info = mla.query_low_level_qs("daily", "30min", last_bi)
        print(f"  30分钟级别内: {qs_info['bi_count']} 笔, {qs_info['zs_count']} 中枢")
        print(f"  趋势: {qs_info['trend_direction'] or '无(盘整)'}")
        print(f"  形成趋势: {qs_info['has_trend']}, 形成盘整: {qs_info['has_consolidation']}")
        print(f"  笔重叠: {qs_info['bi_overlap']}, 可能背驰: {qs_info['divergence_possible']}")

    # --- 查看日线倒数第二笔在 5 分钟中的走势 ---
    if len(daily_result.bis) >= 2:
        prev_bi = daily_result.bis[-2]
        print("\n=== 日线倒数第二笔在 5 分钟中的走势 ===")
        direction = "↑" if prev_bi.direction.value == "up" else "↓"
        print(
            f"日线笔#{prev_bi.index} {direction} "
            f"{prev_bi.start.k.date:%Y-%m-%d} → {prev_bi.end.k.date:%Y-%m-%d}"
        )

        qs_info = mla.query_low_level_qs("daily", "5min", prev_bi)
        print(f"  5分钟级别内: {qs_info['bi_count']} 笔, {qs_info['zs_count']} 中枢")
        print(f"  趋势: {qs_info['trend_direction'] or '无(盘整)'}")
        print(f"  可能背驰: {qs_info['divergence_possible']}")

    # --- 使用自定义配置进行多级别分析 ---
    print("\n=== 自定义配置多级别分析 ===")
    custom_config = ChanlunConfig(
        bi_type="new",
        zs_type="standard",
        fx_strict=True,
    )
    mla2 = MultiLevelAnalyser()
    mla2.add_level("daily", ChanlunAnalyser("SH600519", "daily", config=custom_config))
    mla2.add_level("30min", ChanlunAnalyser("SH600519", "30min", config=custom_config))
    mla2.process("daily", df_daily)
    mla2.process("30min", df_30min)

    all_results = mla2.results()
    for name, res in all_results.items():
        print(f"  {name:6s}: {len(res.bis):3d} 笔, {len(res.zss):3d} 中枢")

    # --- 获取所有级别完整结果 ---
    print("\n=== 所有级别完整结果 ===")
    for name, res in mla.results().items():
        d = res.to_dict()
        print(
            f"  {name:6s}: K线={d['kline_count']} 合并={d['ckline_count']} "
            f"笔={d['bi_count']} 中枢={d['zs_count']} "
            f"线段={d['xd_count']} 买卖点={d['mmd_count']} 背驰={d['bc_count']}"
        )

# 运行结果（示例）:
# === 获取多级别 K 线数据 ===
# 日线: 300 根, 30分钟: 400 根, 5分钟: 500 根
#
# === 各级别缠论分析结果 ===
#   daily :  21 笔,   5 中枢,   4 线段,   3 买卖点
#   30min :  48 笔,  12 中枢,   8 线段,   6 买卖点
#   5min  :  85 笔,  20 中枢,  15 线段,  10 买卖点
#
# === 日线最后一笔在 30 分钟中的走势 ===
# 日线笔#20 ↑ 2025-04-02 → 2025-05-15 高=1521.00 低=1640.00
#   30分钟级别内: 12 笔, 3 中枢
#   趋势: up
#   形成趋势: True, 形成盘整: True
#   笔重叠: False, 可能背驰: True
#
# === 日线倒数第二笔在 5 分钟中的走势 ===
# 日线笔#19 ↓ 2025-03-18 → 2025-04-02
#   5分钟级别内: 28 笔, 4 中枢
#   趋势: down
#   可能背驰: True
