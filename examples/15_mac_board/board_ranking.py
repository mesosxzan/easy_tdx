"""演示：板块涨跌幅排行榜。

通过 MacClient 的 get_board_ranking() 获取行业或概念板块的聚合排行数据，
包含涨跌幅、成交额、成交量、主力净流入、涨跌家数等。

board_type 参数:
    BoardType.HY  — 行业板块
    BoardType.GN  — 概念板块

返回 DataFrame 列:
    code             板块代码
    name             板块名称
    change_pct       涨跌幅%
    amount           板块总成交额（元）
    vol              板块总成交量（股）
    main_net_amount  板块主力净流入（元）
    up_count         上涨家数
    down_count       下跌家数
    member_count     成分股数量
"""

from easy_tdx import MacClient
from easy_tdx.mac.enums import BoardType

with MacClient.from_best_host() as c:
    # 行业板块涨幅
    print("=== 行业板块涨幅 ===")
    df_hy = c.get_board_ranking(BoardType.HY, top_n=300, sort_by="change_pct")
    print(df_hy.to_string(index=False))

    print()

    # 概念板块主力净流入
    print("=== 概念板块主力净流入 ===")
    df_gn = c.get_board_ranking(BoardType.GN, top_n=300, sort_by="main_net_amount")
    print(df_gn.to_string(index=False))

# 运行结果示例:
# === 行业板块涨幅 ===
#  code   name  change_pct        amount          vol  ...
#  881127 通信设备        3.25  18523456000  1234567890  ...
#  881156 半导体          2.98  25678900000  2345678901  ...
#  ...
#
# === 概念板块主力净流入 ===
#  code   name  change_pct        amount          vol  ...
#  880952 人工智能        1.56  42345678000  3456789012  ...
#  880930 芯片概念        1.23  38765432000  2987654321  ...
