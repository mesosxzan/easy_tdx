# 更新日志

本文件记录 easy-tdx 的版本变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

## [1.15.0] — 2026-06-25

### 新增

- **强势股排名（strength）** — 全市场按 5/20/60 日涨幅加权合成强势分，选出"最近最强"的股票。
  - 新增核心引擎 `easy_tdx.screen.strength.StrengthRanker`，纯离线读取本地 `.day` 文件，复用 `SignalScanner` 的并发/进度回调架构。
  - 新增 CLI 子命令 `easy-tdx screen strength`，支持表格 / JSON 输出。
  - 新增 Web API 端点 `GET /api/v1/market/strength`，通过线程池执行避免阻塞事件循环。
  - **三种预设模式**：
    - `steady`（默认）：中长期稳健，60 日权重主导 + 波动率惩罚，选出"稳着涨"的票。
    - `breakout`：近期妖股爆发，5 日权重主导，纯加权涨幅（不除波动率），选出短期最猛的票。
    - `balanced`：三周期均衡 + 波动率调整。
  - 支持自定义权重（自动归一化）、成交额过滤、上市天数过滤、并发扫描。
  - 输出含 `data_date` / `last_date` 字段，标注数据截止日，便于判断时效。
  - 示例代码见 `examples/23_screen_strength/`。

### 修复

- **`_detect_security_type` 代码段判定不全**（`offline/daily_bar.py`）—— 上交所科创板 ETF（588/589）、LOF（560-563）、货币 ETF（551）、普通 ETF（520-530）等代码段，以及深交所封闭式基金/LOF（17/18 开头）、国债逆回购（204 开头）被默认返回值误判为深市 A 股，导致 `screen strength` / `screen scan` 把基金和 ETF 混入股票排名。修复后补全所有已知代码段，默认返回 `UNKNOWN`（不再误判成 A 股）。
- **`screen strength` / `screen rank` 名称补齐分批 bug**（`screen/cli.py`、`screen/ranker.py`）—— `MacClient.get_stock_quotes` 单次最多 80 只，传入超过 80 只时末尾名称被服务器静默丢弃。修复后改为 80 只/批分页查询。

### 变更

- `easy_tdx.screen.__init__` 导出 `StrengthRanker`、`StrengthResult`、`STRENGTH_PRESETS`。
- README 增加「强势股排名（strength）」章节及 Web API 调用示例。

## [1.14.5] — 2026-06-12

- feat(chanlun): 分钟级别日期自适应输出时分 YYYY-MM-DD HH:MM
- release: v1.14.4 — 修复 cmd_chanlun.py ruff format CI 失败
- release: v1.14.3 — 缠论 CLI table 模式补日期（中枢/买卖点/背驰）
- feat(chanlun): CLI table 模式 zss/mmds/bcs 显示日期字段
- release: v1.14.2 — 缠论 JSON 可视化字段增强（中枢/买卖点/背驰补日期）

---

> 历史版本变更请参考 `git log`。
