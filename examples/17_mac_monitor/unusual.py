"""演示：市场异动数据。

通过 MacClient 的 get_unusual() 获取全市场的异动股票数据。

参数:
    market  -- 市场代码（Market.SH / Market.SZ）
    start   -- 起始偏移（默认 0）
    count   -- 单次请求数量（协议上限 600，超出会被截断为 600）

UnusualItem dataclass 字段:
    index         int     异动序号
    market        int     市场代码
    code          str     证券代码
    name          str     证券名称
    time          time    异动时间
    desc          str     异动描述（如 "5分钟涨幅>3%"、"快速拉升"、"大笔买入"）
    value         str     异动数值（如 "3.52%"、"5000手"）
    unusual_type  int     异动类型代码（1=5分钟涨幅, 2=5分钟跌幅, 3=快速拉升, 4=大笔成交等）

返回 DataFrame 列说明:
    index         int      异动序号
    market        int      市场代码
    code          str      证券代码
    name          str      证券名称
    time          object   异动时间（HH:MM:SS 格式）
    desc          str      异动描述
    value         str      异动数值
    unusual_type  int      异动类型代码

说明:
    通达信 MAC 协议 0x1237 单次最多返回 600 条异动数据。
    若要拉取全市场全部异动（盘中可能数千条），需要用 start 参数翻页，
    每次累加 600，直到某页返回不足 600 条即为尾页。
"""

import pandas as pd

from easy_tdx import MacClient, Market

PAGE = 600  # 协议单次返回上限，不要改大（会被截断）

with MacClient.from_best_host() as c:
    frames = []
    start = 0
    while True:
        df = c.get_unusual(Market.SH, start=start, count=PAGE)
        if df.empty:
            break
        frames.append(df)
        if len(df) < PAGE:  # 不足一页 = 已到尾
            break
        start += PAGE

    if frames:
        full = pd.concat(frames, ignore_index=True)
        print(f"共获取 {len(full)} 条异动（{len(frames)} 页）")
        print(full.to_string(index=False))
    else:
        print("暂无异动数据。")

# 示例输出:
#  共获取 1843 条异动（4 页）
#   index  market  code   name       time          desc       value  unusual_type
#       1       1  600XXX  XX科技  09:45:00     5分钟涨幅>3%        3.52%             1
#       2       1  601XXX  XX银行  09:52:00     5分钟涨幅>3%        3.15%             1
#       3       1  600XXX  XX能源  10:05:00     5分钟跌幅>3%       -3.28%             2
#       ...
