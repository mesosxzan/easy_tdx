"""演示：缠论技术分析。

通过 MacClient 获取前复权 K 线数据，执行完整缠论计算管道：
K线合并 → 分型识别 → 笔 → 中枢 → 线段 → 买卖点 → 背驰。

ChanlunAnalyser 接收 easy_tdx 的 K 线 DataFrame（需包含 datetime/open/close/
high/low/vol 列），返回 ChanlunResult，包含所有缠论计算结果。

核心概念:
    笔 (BI)     -- 顶底分型之间的连线，缠论最小分析单元
    中枢 (ZS)   -- 至少三笔重叠区间，多空博弈区域
    线段 (XD)   -- 由多笔构成的更高级别走势
    买卖点 (MMD) -- 1/2/3 类买卖点，基于中枢位置判断
    背驰 (BC)   -- 力度衰减信号，趋势可能反转

ChanlunConfig 可选参数:
    bi_type      -- "new"(新笔,默认) / "old"(老笔) / "simple"(简单笔)
    zs_type      -- "standard"(标准,默认) / "dn"(段内中枢)
    zs_min_lines -- 中枢最少重叠线段数（默认 3）
    fx_strict    -- 是否使用严格分型（默认 True）

参数:
    market    -- 市场代码（Market.SH=1, Market.SZ=0）
    code      -- 股票代码
    period     -- K 线周期（Period 枚举）
    count     -- 返回条数（建议 >= 200 以保证分析有效性）
    adjust    -- 复权方式（默认 QFQ 前复权，技术分析推荐前复权）

返回 ChanlunResult 字段:
    klines    -- 原始 K 线列表
    cklines   -- 合并后缠论 K 线列表
    fractals  -- 分型列表
    bis       -- 笔列表
    zss       -- 中枢列表
    xds       -- 线段列表
    mmds      -- 买卖点列表
    bcs       -- 背驰列表
    macd      -- MACD 数据
"""

from easy_tdx import Adjust, MacClient, Market, Period
from easy_tdx.chanlun import ChanlunAnalyser, ChanlunConfig

with MacClient.from_best_host() as c:
    # --- 日线缠论分析（贵州茅台 600519，前复权，300 条） ---
    print("=== 日线缠论分析（贵州茅台 600988===")
    df = c.get_stock_kline(Market.SH, "600988", Period.DAILY, count=300, adjust=Adjust.QFQ)

    analyser = ChanlunAnalyser(code="SH600988", frequency="daily")
    result = analyser.process_klines(df)

    print(f"原始K线: {len(result.klines)} 根")
    print(f"合并K线: {len(result.cklines)} 根")
    print(f"分型:    {len(result.fractals)} 个")
    print(f"笔:      {len(result.bis)} 笔")
    print(f"中枢:    {len(result.zss)} 个")
    print(f"线段:    {len(result.xds)} 段")
    print(f"买卖点:  {len(result.mmds)} 个")
    print(f"背驰:    {len(result.bcs)} 个")

    # --- 打印最近 5 笔 ---
    print("\n--- 最近 5 笔 ---")
    for bi in result.bis[-5:]:
        direction = "↑" if bi.direction.value == "up" else "↓"
        print(
            f"  笔#{bi.index} {direction} "
            f"{bi.start.k.date:%Y-%m-%d} → {bi.end.k.date:%Y-%m-%d} "
            f"高={bi.high:.2f} 低={bi.low:.2f}"
        )

    # --- 打印中枢 ---
    print("\n--- 中枢 ---")
    for zs in result.zss:
        start_date = zs.start.k.date.strftime("%Y-%m-%d") if zs.start else "N/A"
        end_date = zs.end.k.date.strftime("%Y-%m-%d") if zs.end else "N/A"
        print(
            f"  中枢#{zs.index} [{start_date} → {end_date}] "
            f"上沿={zs.zg:.2f} 下沿={zs.zd:.2f} "
            f"最高={zs.gg:.2f} 最低={zs.dd:.2f} "
            f"笔数={zs.line_count} 完成={zs.done}"
        )

    # --- 打印买卖点 ---
    if result.mmds:
        print("\n--- 买卖点 ---")
        for mmd in result.mmds:
            date_str = mmd.bi.end.k.date.strftime("%Y-%m-%d") if mmd.bi else "N/A"
            print(f"  {mmd.mmd_type.value:6s} 日期={date_str}  {mmd.msg}")

    # --- 打印背驰 ---
    if result.bcs:
        print("\n--- 背驰 ---")
        for bc in result.bcs:
            curr_date = bc.curr.end.k.date.strftime("%Y-%m-%d") if bc.curr else "N/A"
            prev_date = bc.prev.end.k.date.strftime("%Y-%m-%d") if bc.prev else "N/A"
            print(
                f"  {bc.bc_type.value:4s} 背驰={bc.bc!s:5} "
                f"当前={curr_date} 前={prev_date}  {bc.msg}"
            )

    # --- 使用自定义配置 ---
    print("\n=== 使用自定义配置（老笔 + 段内中枢）===")
    custom_config = ChanlunConfig(
        bi_type="old",
        zs_type="dn",
        zs_min_lines=3,
        fx_strict=True,
    )
    analyser2 = ChanlunAnalyser(code="SH600519", frequency="daily", config=custom_config)
    result2 = analyser2.process_klines(df)
    print(f"老笔模式: {len(result2.bis)} 笔, {len(result2.zss)} 个中枢")

    # --- 周线缠论分析 ---
    print("\n=== 周线缠论分析（贵州茅台 600519）===")
    df_weekly = c.get_stock_kline(Market.SH, "600519", Period.WEEKLY, count=200, adjust=Adjust.QFQ)
    analyser_w = ChanlunAnalyser(code="SH600519", frequency="weekly")
    result_w = analyser_w.process_klines(df_weekly)
    print(f"周线: {len(result_w.klines)} 根K线, {len(result_w.bis)} 笔, {len(result_w.zss)} 个中枢")

    # --- 通过 to_dict() 获取完整可序列化结果 ---
    print("\n=== to_dict() 输出（截取前 3 笔）===")
    d = result.to_dict()
    print(f"code={d['code']}  frequency={d['frequency']}")
    print(f"统计: K线={d['kline_count']} 合并={d['ckline_count']} "
          f"分型={d['fractal_count']} 笔={d['bi_count']} "
          f"中枢={d['zs_count']} 线段={d['xd_count']}")
    for bi_dict in d["bis"][:3]:
        print(f"  笔#{bi_dict['index']} {bi_dict['direction']:4s} "
              f"{bi_dict['start_date']} → {bi_dict['end_date']}")

# 运行结果（示例）:
# === 日线缠论分析（贵州茅台 600519）===
# 原始K线: 300 根
# 合并K线: 187 根
# 分型:    42 个
# 笔:      21 笔
# 中枢:    5 个
# 线段:    4 段
# 买卖点:  3 个
# 背驰:    2 个
#
# --- 最近 5 笔 ---
#   笔#16 ↑ 2025-02-10 → 2025-02-20 高=1680.00 低=1560.00
#   笔#17 ↓ 2025-02-20 → 2025-03-05 高=1680.00 低=1590.00
#   笔#18 ↑ 2025-03-05 → 2025-03-18 高=1720.00 低=1590.00
#   笔#19 ↓ 2025-03-18 → 2025-04-02 高=1720.00 低=1640.00
#   笔#20 ↑ 2025-04-02 → 2025-05-15 高=1521.00 低=1640.00
#
# --- 中枢 ---
#   中枢#0 [2024-08-12 → 2024-09-20] 上沿=1450.00 下沿=1380.00
#       最高=1480.00 最低=1360.00 笔数=3 完成=True
#   中枢#1 [2024-10-15 → 2024-12-03] 上沿=1560.00 下沿=1490.00
#       最高=1600.00 最低=1470.00 笔数=3 完成=True
#   ...
#
# --- 买卖点 ---
#   1buy  日期=2025-02-10  中枢#1 第三类买点
#   2buy  日期=2025-03-05  中枢#2 第二类买点
#   3buy  日期=2025-04-02  中枢#3 第三类买点
#
# --- 背驰 ---
#   bi   背驰=True 当前=2025-03-18 前=2025-02-20  笔背驰，力度衰减
#   pz   背驰=False 当前=2025-05-15 前=2025-04-02  无盘整背驰
