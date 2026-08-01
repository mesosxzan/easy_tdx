<script setup lang="ts">
// 问财批量回测页面：左配置（问财搜索 + 策略 + 资金 + 日期）/ 右结果（汇总 + 逐标的表格）。
// 核心交互：在问财搜索栏旁放置「批量回测」按钮，点击后后端自动搜索 + 逐个独立回测 + 汇总。

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import EquityChart from '../components/EquityChart.vue'
import KlineChart from '../components/KlineChart.vue'
import MetricTable from '../components/MetricTable.vue'
import StrategyPicker from '../components/StrategyPicker.vue'
import TradeTable from '../components/TradeTable.vue'
import WencaiSourcePanel from '../components/WencaiSourcePanel.vue'
import { normalizeBar } from '../api'
import { useBacktestStore } from '../stores/backtest'
import type { Bar, Category, ExecutionMode, Performance, WencaiBacktestStockResult } from '../types'

const store = useBacktestStore()

// ── 表单状态 ──────────────────────────────────────────────────────────────────
const strategy = ref('ma_cross')
const params = ref<Record<string, number | string | boolean>>({})
const cash = ref(100_000)
const commission = ref(0.0003)
const slippage = ref(0)
const execution = ref<ExecutionMode>('next_open')
const category = ref<Category>('DAY')
const top = ref(10)
const count = ref(250)

function isoDaysFromNow(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}
const startDate = ref('2020-01-06')
const endDate = ref(isoDaysFromNow(0))

const EXECUTIONS: { value: ExecutionMode; label: string }[] = [
  { value: 'next_open', label: '开盘价' },
  { value: 'next_close', label: '收盘价' },
]
const CATEGORIES: Category[] = ['DAY', 'WEEK', 'MONTH', 'MIN_5', 'MIN_15', 'MIN_30', 'MIN_60']

onMounted(async () => {
  await store.loadStrategies().catch((e) => {
    store.error = `加载策略列表失败：${e instanceof Error ? e.message : e}`
  })
  window.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeyDown)
})

const strategyLabel = computed(
  () => store.strategies.find((s) => s.name === strategy.value)?.label ?? strategy.value,
)

// ── 批量回测（由 WencaiSourcePanel 的「批量回测」按钮触发）────────────────────
async function onBatchBacktest(query: string) {
  store.clearWencai()
  await store.runWencai({
    query,
    top: top.value,
    strategy: strategy.value,
    params: params.value,
    cash: cash.value,
    commission: commission.value,
    slippage: slippage.value,
    execution: execution.value,
    category: category.value,
    count: count.value,
    start_date: startDate.value,
    end_date: endDate.value,
  })
}

// ── 结果展示辅助 ──────────────────────────────────────────────────────────────
function fmtPct(v: number | undefined | null): string {
  if (v == null || isNaN(v)) return '--'
  return (v * 100).toFixed(2) + '%'
}

function fmtNum(v: number | undefined | null, digits = 2): string {
  if (v == null || isNaN(v)) return '--'
  return v.toFixed(digits)
}

function retClass(v: number | undefined | null): string {
  if (v == null || isNaN(v)) return ''
  return v > 0 ? 'pos' : v < 0 ? 'neg' : ''
}

// 按总收益降序排列结果（出错的排最后）
const sortedResults = computed(() => {
  if (!store.wencaiResult) return []
  return [...store.wencaiResult.results].sort((a, b) => {
    if (a.error && !b.error) return 1
    if (!a.error && b.error) return -1
    const ra = a.performance.total_return ?? -Infinity
    const rb = b.performance.total_return ?? -Infinity
    return rb - ra
  })
})

// ── 选中标的与键盘导航（↑↓ 切换）──────────────────────────────────────────
const selectedIndex = ref(0)

/** 当前选中的标的（已排序结果中的第 selectedIndex 项）。 */
const selectedResult = computed<WencaiBacktestStockResult | null>(() => {
  const rows = sortedResults.value
  if (rows.length === 0) return null
  return rows[Math.min(selectedIndex.value, rows.length - 1)]
})

/** 选中标的的 OHLCV bars（后端返回原始 dict，需 normalizeBar 转为 Bar[]）。 */
const selectedBars = computed<Bar[]>(() => {
  const raw = selectedResult.value?.bars
  if (!raw || raw.length === 0) return []
  return raw.map((r) => normalizeBar(r as unknown as Record<string, unknown>))
})

/** 选中标的的绩效指标（补齐默认值，满足 MetricTable 的 Performance 类型约束）。 */
const selectedPerf = computed<Performance>(() => ({
  total_return: 0,
  annual_return: 0,
  max_drawdown: 0,
  max_dd_duration: 0,
  sharpe: 0,
  sortino: 0,
  calmar: 0,
  total_trades: 0,
  win_trades: 0,
  lose_trades: 0,
  rejected_trades: 0,
  win_rate: 0,
  profit_factor: 0,
  avg_win: 0,
  avg_loss: 0,
  max_win: 0,
  max_loss: 0,
  avg_holding_days: 0,
  volatility: 0,
  buy_hold_return: 0,
  ...selectedResult.value?.performance,
}))

/** 上下键切换标的。输入框/文本域/下拉框聚焦时不拦截方向键。 */
function onKeyDown(e: KeyboardEvent) {
  if (!store.wencaiResult || sortedResults.value.length === 0) return
  const t = e.target as HTMLElement
  if (
    t instanceof HTMLInputElement ||
    t instanceof HTMLTextAreaElement ||
    t instanceof HTMLSelectElement
  ) {
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (selectedIndex.value < sortedResults.value.length - 1) {
      selectedIndex.value++
      scrollSelectedIntoView()
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (selectedIndex.value > 0) {
      selectedIndex.value--
      scrollSelectedIntoView()
    }
  }
}

function scrollSelectedIntoView() {
  nextTick(() => {
    document
      .querySelector('.result-table tbody tr.selected')
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
}

// 新回测结果到达时重置选中索引到第一只
watch(
  () => store.wencaiResult,
  () => {
    selectedIndex.value = 0
  },
)
</script>

<template>
  <div class="wencai-backtest-view">
    <!-- 左栏：配置 -->
    <aside class="config-panel">
      <section class="panel-section">
        <h3>问财搜索</h3>
        <WencaiSourcePanel
          mode="multi"
          :show-backtest-btn="true"
          :backtest-loading="store.wencaiRunning"
          @backtest="onBatchBacktest"
        />
      </section>

      <section class="panel-section">
        <h3>策略</h3>
        <StrategyPicker
          v-if="store.strategies.length"
          :strategies="store.strategies"
          v-model:strategy="strategy"
          v-model:params="params"
        />
        <p v-else class="loading-text">加载策略中…</p>
      </section>

      <section class="panel-section">
        <h3>资金与成本</h3>
        <div class="row">
          <div class="field">
            <label>每只资金</label>
            <input v-model.number="cash" type="number" min="1000" step="10000" />
          </div>
          <div class="field">
            <label>取前 N 只</label>
            <input v-model.number="top" type="number" min="1" max="50" />
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>佣金率</label>
            <input v-model.number="commission" type="number" min="0" step="0.0001" />
          </div>
          <div class="field">
            <label>滑点</label>
            <input v-model.number="slippage" type="number" min="0" step="0.001" />
          </div>
        </div>
        <div class="field">
          <label>成交价</label>
          <select v-model="execution">
            <option v-for="e in EXECUTIONS" :key="e.value" :value="e.value">{{ e.label }}</option>
          </select>
        </div>
      </section>

      <section class="panel-section">
        <h3>周期与日期</h3>
        <div class="row">
          <div class="field">
            <label>周期</label>
            <select v-model="category">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="field">
            <label>K 线根数</label>
            <input v-model.number="count" type="number" min="20" max="2000" step="50" />
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>开始日期</label>
            <input v-model="startDate" type="date" />
          </div>
          <div class="field">
            <label>结束日期</label>
            <input v-model="endDate" type="date" />
          </div>
        </div>
      </section>
    </aside>

    <!-- 右栏：结果 -->
    <main class="report-panel">
      <div v-if="store.error" class="error-banner">⚠ {{ store.error }}</div>

      <div
        v-if="!store.wencaiResult && !store.wencaiRunning && !store.error"
        class="placeholder"
      >
        <p>输入问财查询语句，配置策略后点击「批量回测」</p>
      </div>

      <div v-if="store.wencaiRunning" class="placeholder">
        <p>问财批量回测中…（搜索 + 逐标的取行情 + 回测）</p>
      </div>

      <div v-if="store.wencaiResult" class="report-content">
        <!-- 汇总统计 -->
        <section class="report-section">
          <h3>汇总统计</h3>
          <div class="summary-grid">
            <div class="summary-item">
              <span class="label">查询语句</span>
              <span class="value query-text">{{ store.wencaiResult.query }}</span>
            </div>
            <div class="summary-item">
              <span class="label">策略</span>
              <span class="value">{{ strategyLabel }}</span>
            </div>
            <div class="summary-item">
              <span class="label">搜索标的</span>
              <span class="value">{{ store.wencaiResult.stocks_searched }}</span>
            </div>
            <div class="summary-item">
              <span class="label">成功回测</span>
              <span class="value">{{ store.wencaiResult.stocks_backtested }}</span>
            </div>
          </div>
          <div class="summary-grid" v-if="store.wencaiResult.summary">
            <div class="summary-item">
              <span class="label">平均收益</span>
              <span
                class="value"
                :class="retClass(store.wencaiResult.summary.avg_return)"
              >{{ fmtPct(store.wencaiResult.summary.avg_return) }}</span>
            </div>
            <div class="summary-item">
              <span class="label">平均夏普</span>
              <span class="value">{{ fmtNum(store.wencaiResult.summary.avg_sharpe) }}</span>
            </div>
            <div class="summary-item">
              <span class="label">平均最大回撤</span>
              <span class="value neg">{{ fmtPct(store.wencaiResult.summary.avg_max_drawdown) }}</span>
            </div>
            <div class="summary-item">
              <span class="label">盈亏比</span>
              <span class="value">
                <span class="pos">{{ store.wencaiResult.summary.positive_count }}</span>
                /
                <span class="neg">{{ store.wencaiResult.summary.negative_count }}</span>
              </span>
            </div>
          </div>
          <div
            class="best-worst"
            v-if="store.wencaiResult.summary?.best && store.wencaiResult.summary?.worst"
          >
            <div class="bw-item">
              <span class="bw-label">最佳：</span>
              <span class="bw-code">{{ store.wencaiResult.summary.best.symbol }}</span>
              <span class="bw-name">{{ store.wencaiResult.summary.best.name }}</span>
              <span class="pos">{{ fmtPct(store.wencaiResult.summary.best.return) }}</span>
            </div>
            <div class="bw-item">
              <span class="bw-label">最差：</span>
              <span class="bw-code">{{ store.wencaiResult.summary.worst.symbol }}</span>
              <span class="bw-name">{{ store.wencaiResult.summary.worst.name }}</span>
              <span class="neg">{{ fmtPct(store.wencaiResult.summary.worst.return) }}</span>
            </div>
          </div>
        </section>

        <!-- 选中标的详情（↑↓ 键切换）-->
        <section v-if="selectedResult" class="report-section detail-section">
          <h3 class="detail-title">
            <span>{{ selectedResult.market }}:{{ selectedResult.symbol }}</span>
            <span class="detail-name">{{ selectedResult.name }}</span>
            <span class="kb-hint">↑↓ 切换标的</span>
          </h3>
          <div v-if="selectedResult.error" class="err-detail">
            回测失败：{{ selectedResult.error }}
          </div>
          <template v-else>
            <div class="detail-sub">
              <h4>K线 · 均线 · 成交量 · 买卖点</h4>
              <KlineChart
                :bars="selectedBars"
                :trades="selectedResult.trades"
                :title="`${selectedResult.market}:${selectedResult.symbol} ${selectedResult.name}`"
              />
            </div>
            <div class="detail-sub">
              <h4>净值曲线与回撤</h4>
              <EquityChart :equity="selectedResult.equity_curve" />
            </div>
            <div class="detail-sub">
              <h4>绩效指标</h4>
              <MetricTable :perf="selectedPerf" />
            </div>
            <div class="detail-sub">
              <h4>成交记录（{{ selectedResult.trades.length }} 笔）</h4>
              <TradeTable :trades="selectedResult.trades" />
            </div>
          </template>
        </section>

        <!-- 逐标的回测结果 -->
        <section class="report-section">
          <h3>
            各标的回测结果（{{ sortedResults.length }} 只，按收益降序）
            <span class="kb-hint">↑↓ 切换 · 点击行选中</span>
          </h3>
          <div class="table-wrap">
            <table class="result-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th class="num">总收益</th>
                  <th class="num">年化</th>
                  <th class="num">夏普</th>
                  <th class="num">最大回撤</th>
                  <th class="num">胜率</th>
                  <th class="num">交易数</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, i) in sortedResults"
                  :key="row.symbol"
                  :class="{ selected: i === selectedIndex }"
                  @click="selectedIndex = i"
                >
                  <td class="code">{{ row.symbol }}</td>
                  <td class="name">{{ row.name }}</td>
                  <td
                    v-if="!row.error"
                    class="num"
                    :class="retClass(row.performance.total_return)"
                  >{{ fmtPct(row.performance.total_return) }}</td>
                  <td v-else class="num">--</td>
                  <td v-if="!row.error" class="num">{{ fmtPct(row.performance.annual_return) }}</td>
                  <td v-else class="num">--</td>
                  <td v-if="!row.error" class="num">{{ fmtNum(row.performance.sharpe) }}</td>
                  <td v-else class="num">--</td>
                  <td v-if="!row.error" class="num neg">
                    {{ fmtPct(row.performance.max_drawdown) }}
                  </td>
                  <td v-else class="num">--</td>
                  <td v-if="!row.error" class="num">{{ fmtPct(row.performance.win_rate) }}</td>
                  <td v-else class="num">--</td>
                  <td v-if="!row.error" class="num">{{ row.performance.total_trades ?? '--' }}</td>
                  <td v-else class="num">--</td>
                  <td :class="row.error ? 'err-status' : 'ok-status'">
                    {{ row.error ? '失败' : '成功' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="sortedResults.some((r) => r.error)" class="err-hint">
            部分标的回测失败（行情不足或策略异常），已跳过并标注「失败」。
          </p>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.wencai-backtest-view {
  display: flex;
  height: 100%;
}

/* 左栏配置面板 */
.config-panel {
  width: 360px;
  flex-shrink: 0;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  padding: 16px;
  overflow-y: auto;
}
.panel-section {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.panel-section:last-of-type {
  border-bottom: none;
}
.panel-section h3 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 12px;
}
.loading-text {
  color: var(--text-dim);
  font-size: 12px;
}
.row {
  display: flex;
  gap: 8px;
}
.row .field {
  flex: 1;
}

/* 右栏报告面板 */
.report-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-dim);
}
.error-banner {
  background: rgba(239, 65, 70, 0.12);
  border: 1px solid var(--up);
  color: var(--up);
  padding: 10px 14px;
  border-radius: var(--radius);
  margin-bottom: 16px;
  font-size: 13px;
}
.report-section {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.report-section h3 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 12px;
}

/* 汇总统计 */
.summary-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  margin-bottom: 12px;
}
.summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.summary-item .label {
  font-size: 11px;
  color: var(--text-dim);
}
.summary-item .value {
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-mono);
}
.query-text {
  font-size: 13px;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.best-worst {
  display: flex;
  gap: 24px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.bw-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.bw-label {
  color: var(--text-dim);
}
.bw-code {
  font-family: var(--font-mono);
  font-weight: 600;
}
.bw-name {
  color: var(--text-muted);
}

/* 结果表格 */
.table-wrap {
  overflow-x: auto;
}
.result-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.result-table th,
.result-table td {
  padding: 7px 10px;
  text-align: left;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.result-table th {
  color: var(--text-dim);
  font-weight: 500;
  white-space: nowrap;
}
.result-table td.num {
  text-align: right;
  font-family: var(--font-mono);
}
.result-table td.code {
  font-family: var(--font-mono);
  font-weight: 600;
}
.result-table td.name {
  color: var(--text-muted);
}
.result-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.02);
}
.pos {
  color: var(--up);
}
.neg {
  color: var(--down);
}
.ok-status {
  color: var(--down);
  font-size: 11px;
}
.err-status {
  color: var(--up);
  font-size: 11px;
}
.err-hint {
  margin-top: 8px;
  color: var(--text-dim);
  font-size: 11px;
}

/* 表格行：可点击 + 选中高亮 */
.result-table tbody tr {
  cursor: pointer;
}
.result-table tbody tr.selected {
  background: rgba(245, 158, 11, 0.08);
}
.result-table tbody tr.selected td.code {
  color: var(--accent);
}

/* 键盘操作提示 */
.kb-hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-dim);
  margin-left: 8px;
}

/* 选中标的详情面板 */
.detail-section {
  border-color: rgba(245, 158, 11, 0.25);
}
.detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-title > span:first-child {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--accent);
}
.detail-name {
  color: var(--text-muted);
  font-weight: 400;
}
.detail-sub {
  margin-bottom: 16px;
}
.detail-sub:last-child {
  margin-bottom: 0;
}
.detail-sub h4 {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-dim);
  margin-bottom: 8px;
}
.err-detail {
  padding: 12px 14px;
  background: rgba(239, 65, 70, 0.08);
  border: 1px solid var(--up);
  border-radius: var(--radius);
  color: var(--up);
  font-size: 13px;
}
</style>
