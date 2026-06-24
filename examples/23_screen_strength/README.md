# 23. 强势股排名（screen strength）

按 **5 / 20 / 60 日涨幅加权**合成强势分，从全市场选出"最近最强"的股票。

## 三种预设模式

| 模式 | 性格 | 适合 |
|------|------|------|
| `steady` | 中长期稳健（60日主导 + 波动率惩罚） | 选"稳着涨"的票 |
| `breakout` | 近期妖股爆发（5日主导，纯涨幅） | 选"短期最猛"的票 |
| `balanced` | 三周期均衡 + 波动率调整 | 不确定时的安全默认 |

## 前提条件

需要本地通达信 `.day` 日线数据（扫描纯离线，无网络请求）：

```bash
# 同步最新日线数据
easy-tdx offline sync

# 或用通达信客户端下载日线数据到 vipdoc/{sh,sz}/lday/
```

## 示例文件

| 文件 | 说明 |
|------|------|
| `strength_api.py` | Python API 调用（`StrengthRanker` 类） |
| `strength_cli.sh` | CLI 命令示例（`easy-tdx screen strength`） |
| `strength_web_api.py` | Web API 调用（`GET /api/v1/market/strength`） |

## 快速开始

### Python API

```python
from easy_tdx.screen.strength import StrengthRanker

ranker = StrengthRanker(preset="steady")
results = ranker.rank(top_n=20)
for r in results[:5]:
    print(f"#{r.rank} {r.market}{r.code} 强势分={r.strength:.2f}")
```

### CLI

```bash
# 表格输出
easy-tdx screen strength --preset steady --top 50 --table

# 近期妖股 + 补齐名称
easy-tdx screen strength --preset breakout --top 20 --names --table

# 自定义权重（自动归一化）
easy-tdx screen strength --w5 0.5 --w20 0.3 --w60 0.2 --top 30 --table
```

### Web API

```bash
# 启动服务
easy-tdx serve

# 调用接口
curl "http://localhost:8000/api/v1/market/strength?preset=breakout&top_n=20"
```

## 输出字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | int | 排名 |
| `code` | str | 6 位股票代码 |
| `market` | str | 市场（SZ/SH） |
| `name` | str | 股票名称（需 `--names` 开启） |
| `last_close` | float | 最新收盘价 |
| `last_date` | int | 数据截止日（YYYYMMDD） |
| `ret_5` | float | 5 日涨幅 |
| `ret_20` | float | 20 日涨幅 |
| `ret_60` | float | 60 日涨幅 |
| `vol_20` | float | 20 日波动率（对数收益率标准差） |
| `strength` | float | 强势综合分（排序依据） |

## 公式

```
ret_5  = close[-1] / close[-6]  - 1
ret_20 = close[-1] / close[-21] - 1
ret_60 = close[-1] / close[-61] - 1
vol_20 = std(log_return, 20)[-1]

strength = (w5·ret_5 + w20·ret_20 + w60·ret_60) / vol_20   # vol_adjusted=True
strength =  w5·ret_5 + w20·ret_20 + w60·ret_60              # vol_adjusted=False
```

权重自动归一化：`w = w / (w5 + w20 + w60)`。
