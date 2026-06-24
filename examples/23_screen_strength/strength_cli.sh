#!/bin/bash
# easy-tdx 强势股排名 — CLI 使用示例
#
# 前提：本地通达信 vipdoc/{sh,sz}/lday/*.day 已同步最新数据
#       可用 `easy-tdx offline sync` 同步
#
# 三种预设模式：
#   steady   — 中长期稳健（60日主导 + 波动率惩罚），选稳着涨的票
#   breakout — 近期妖股爆发（5日主导，纯涨幅），选最猛的票
#   balanced — 三周期均衡 + 波动率调整
#
# 用法：去掉命令前的 # 即可实际执行。

echo "================================================================"
echo "1. steady 模式 — 中长期稳健强势 Top 50（表格输出）"
echo "================================================================"
# easy-tdx screen strength --preset steady --top 50 --table

echo ""
echo "================================================================"
echo "2. breakout 模式 — 近期妖股爆发 Top 20（补齐股票名称）"
echo "================================================================"
# easy-tdx screen strength --preset breakout --top 20 --names --table

echo ""
echo "================================================================"
echo "3. balanced 模式 — 三周期均衡 Top 30"
echo "================================================================"
# easy-tdx screen strength --preset balanced --top 30 --table

echo ""
echo "================================================================"
echo "4. 自定义权重（自动归一化，5:3:2 = 0.5:0.3:0.2）"
echo "================================================================"
# easy-tdx screen strength --w5 0.5 --w20 0.3 --w60 0.2 --top 30 --table

echo ""
echo "================================================================"
echo "5. 自定义权重 + 关闭波动率惩罚（纯加权涨幅）"
echo "================================================================"
# easy-tdx screen strength --w5 0.6 --w20 0.3 --w60 0.1 --no-vol-adjusted --top 20 --table

echo ""
echo "================================================================"
echo "6. 并发扫描（4 进程，速度提升约 4 倍）"
echo "================================================================"
# easy-tdx screen strength --preset steady --top 100 --workers 4 --table

echo ""
echo "================================================================"
echo "7. 过滤低流动性（最近 5 日日均成交额 ≥ 5000 万）"
echo "================================================================"
# easy-tdx screen strength --preset breakout --top 30 --min-amount 50000000 --table

echo ""
echo "================================================================"
echo "8. 缩小范围（仅深圳）+ 输出到 JSON 文件"
echo "================================================================"
# easy-tdx screen strength --universe sz --top 30 --output sz_strength.json

echo ""
echo "================================================================"
echo "9. 仅上海 + 最小上市天数 120 日（过滤次新股）"
echo "================================================================"
# easy-tdx screen strength --universe sh --min-listed-days 120 --top 30 --table

echo ""
echo "================================================================"
echo "10. 对比三种预设（同一批股票，不同视角）"
echo "================================================================"
echo "--- steady（稳健）---"
# easy-tdx screen strength --preset steady --top 10 --table
echo ""
echo "--- breakout（妖股）---"
# easy-tdx screen strength --preset breakout --top 10 --table
echo ""
echo "--- balanced（均衡）---"
# easy-tdx screen strength --preset balanced --top 10 --table
