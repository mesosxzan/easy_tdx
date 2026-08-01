<script setup lang="ts">
import { computed, ref } from 'vue'

import { fetchWencaiSearch, formatError } from '../api'
import { marketLabel } from '../market'
import type { Market } from '../market'

type WencaiActionRow = {
  symbol: string
  market: Market
  name: string
  stockReason: string
  displaySymbol: string
}

const props = withDefaults(defineProps<{
  mode?: 'single' | 'multi'
  selectedCodes?: string[]
  showBacktestBtn?: boolean
  backtestLoading?: boolean
  /** 是否在每行显示单标【回测】按钮（单标回测页问财模式用） */
  showStockBacktestBtn?: boolean
  /** 单标回测进行中（禁用行内【回测】按钮，避免并发） */
  stockBacktestLoading?: boolean
}>(), {
  mode: 'single',
  selectedCodes: () => [],
  showBacktestBtn: false,
  backtestLoading: false,
  showStockBacktestBtn: false,
  stockBacktestLoading: false,
})

const emit = defineEmits<{
  pick: [code: string]
  add: [symbol: string]
  backtest: [query: string]
  backtestStock: [row: WencaiActionRow]
}>()

const query = ref('')
const loading = ref(false)
const error = ref('')
const searched = ref(false)
const results = ref<WencaiActionRow[]>([])

const selectedCodeSet = computed(() => new Set(props.selectedCodes.map(extractCodeFromSymbol)))

function extractCodeFromSymbol(value: string): string {
  const m = value.match(/(\d{6})$/)
  return m ? m[1] : value
}

function toMarket(m: string): Market {
  if (m === 'SH' || m === 'SZ' || m === 'BJ') return m
  return 'SZ'
}

function normalizeRows(rows: Record<string, unknown>[]): WencaiActionRow[] {
  return rows.map((row) => {
    const symbol = String(row.symbol ?? '')
    const market = toMarket(String(row.market ?? 'SZ'))
    return {
      symbol,
      market,
      name: String(row.name ?? '--'),
      stockReason: String(row.stock_reason ?? ''),
      displaySymbol: `${market}:${symbol}`,
    }
  })
}

async function search() {
  const trimmed = query.value.trim()
  if (!trimmed) {
    error.value = '请输入问财查询语句'
    results.value = []
    searched.value = false
    return
  }

  loading.value = true
  error.value = ''
  searched.value = true
  try {
    const rows = await fetchWencaiSearch({ query: trimmed })
    results.value = normalizeRows(rows)
  } catch (e) {
    error.value = formatError(e)
    results.value = []
  } finally {
    loading.value = false
  }
}

function onUse(row: WencaiActionRow) {
  if (props.mode === 'single') {
    emit('pick', row.symbol)
    return
  }
  emit('add', row.displaySymbol)
}

function onBacktest() {
  const trimmed = query.value.trim()
  if (!trimmed) return
  emit('backtest', trimmed)
}

/** 行内单标【回测】：把该行标的交给父组件（填代码 + 取行情 + 回测）。 */
function onStockBacktest(row: WencaiActionRow) {
  emit('backtestStock', row)
}

function actionLabel(row: WencaiActionRow): string {
  if (props.mode === 'single') return '使用'
  return selectedCodeSet.value.has(row.symbol) ? '已添加' : '加入'
}

function isDisabled(row: WencaiActionRow): boolean {
  return props.mode === 'multi' && selectedCodeSet.value.has(row.symbol)
}
</script>

<template>
  <div class="wencai-panel">
    <div class="field">
      <label>问财语句</label>
      <div class="search-row">
        <input
          v-model="query"
          placeholder="例：近20日涨幅前20，非ST，成交额大于5亿"
          @keyup.enter="search"
        />
        <button :disabled="loading" @click="search">
          {{ loading ? '搜索中…' : '问财搜索' }}
        </button>
        <button
          v-if="showBacktestBtn"
          class="backtest-btn"
          :disabled="backtestLoading || !query.trim()"
          @click="onBacktest"
        >
          {{ backtestLoading ? '回测中…' : '批量回测' }}
        </button>
        <!-- 父组件可注入额外操作按钮（如单标回测页的「开始回测」） -->
        <slot name="actions" />
      </div>
      <p class="hint">
        通过同花顺问财结果挑选标的，默认取股票结果前 100 条。首次使用请先到「服务器设置」保存问财 Cookie。
      </p>
    </div>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-else-if="searched && !loading && results.length === 0" class="hint">没有可用结果</p>

    <div v-if="results.length" class="result-wrap">
      <div class="result-head">
        <span>共识别 {{ results.length }} 只可用标的</span>
      </div>
      <div class="result-list">
        <div v-for="row in results" :key="row.displaySymbol" class="result-item">
          <div class="main">
            <div class="title-row">
              <strong>{{ row.symbol }}</strong>
              <span class="name">{{ row.name }}</span>
              <span class="market-tag">{{ marketLabel(row.market) }}</span>
            </div>
            <div v-if="row.stockReason" class="meta-row">
              <span>{{ row.stockReason }}</span>
            </div>
          </div>
          <div class="row-actions">
            <button class="ghost-btn" :disabled="isDisabled(row)" @click="onUse(row)">
              {{ actionLabel(row) }}
            </button>
            <button
              v-if="showStockBacktestBtn"
              class="stock-backtest-btn"
              :disabled="stockBacktestLoading"
              @click="onStockBacktest(row)"
            >
              {{ stockBacktestLoading ? '回测中…' : '回测' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-row {
  display: flex;
  gap: 8px;
}
.search-row input {
  flex: 1;
}
.backtest-btn {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  white-space: nowrap;
}
.backtest-btn:hover:not(:disabled) {
  opacity: 0.85;
}
.backtest-btn:disabled {
  opacity: 0.5;
}
.hint {
  margin-top: 6px;
  color: var(--text-dim);
  font-size: 12px;
}
.err {
  margin-top: 8px;
  color: var(--up);
  font-size: 12px;
}
.result-wrap {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.01);
}
.result-head {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
}
.result-list {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow: auto;
}
.result-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.result-item:last-child {
  border-bottom: none;
}
.main {
  min-width: 0;
  flex: 1;
}
.title-row,
.meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.name {
  color: var(--text-muted);
}
.meta-row {
  margin-top: 4px;
  color: var(--text-dim);
  font-size: 12px;
}
.market-tag {
  font-size: 11px;
  color: var(--text-dim);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 3px;
}
.ghost-btn {
  min-width: 54px;
  padding: 5px 10px;
}
.row-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.stock-backtest-btn {
  min-width: 54px;
  padding: 5px 10px;
  background: var(--accent);
  color: #fff;
  border: 1px solid var(--accent);
  white-space: nowrap;
}
.stock-backtest-btn:hover:not(:disabled) {
  opacity: 0.85;
}
.stock-backtest-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
