// 股票搜索 composable：模块级缓存搜索索引 + 按代码/名字/声母三路过滤。
// 索引整会话只拉一次（~150KB / 5000 条），后续过滤纯本地计算（<5ms）。
//
// 加载策略：App 根组件挂载时调 eagerLoad() 立即开始拉取，期间 AppInitOverlay
// 全局遮罩盖住页面（"正在初始化股票列表…"），就绪后遮罩消失。这样既保证
// 用户进入页面时搜索已可用，又给了清晰的初始化反馈。
// 后端 lifespan 也会后台预热 get_security_list_all 缓存，多数情况下 eagerLoad
// 能秒回（命中后端已建好的缓存）。

import { ref } from 'vue'

import { fetchSearchIndex, formatError } from '../api'
import type { StockSearchEntry } from '../types'

// ── 模块级状态（所有组件实例共享） ───────────────────────────────────────────
let cachedIndex: StockSearchEntry[] | null = null
let loadPromise: Promise<StockSearchEntry[]> | null = null

/** 全局响应式状态：所有 useStockSearch 实例共享同一组 ref，保证遮罩与输入框一致 */
const ready = ref(false)
const loading = ref(false)
const failed = ref(false)
const loadError = ref('')

/** 三路匹配：代码前缀 / 名字包含 / 声母包含。 */
function matchEntry(entry: StockSearchEntry, q: string): boolean {
  if (entry.code.startsWith(q)) return true
  if (entry.name.includes(q)) return true
  if (entry.initials.includes(q)) return true
  return false
}

/** 拉索引（去重并发请求；成功后常驻模块级缓存）。失败抛错。 */
async function ensureIndex(): Promise<StockSearchEntry[]> {
  if (cachedIndex) return cachedIndex
  if (!loadPromise) {
    loadPromise = (async () => {
      const { data } = await fetchSearchIndex()
      cachedIndex = data
      return data
    })().catch((e) => {
      loadPromise = null // 失败清空，允许下次重试
      throw e
    })
  }
  return loadPromise
}

export interface UseStockSearch {
  /** 索引是否已加载就绪 */
  ready: typeof ready
  /** 是否正在加载（遮罩用） */
  loading: typeof loading
  /** 是否加载失败（遮罩用：失败也要消失，不能卡死） */
  failed: typeof failed
  /** 加载错误信息 */
  loadError: typeof loadError
  /** App 根挂载时调用，立即开始拉取索引 */
  eagerLoad: () => void
  /** 按输入过滤，返回最多 limit 条（默认 30）。索引未就绪时返回空数组。 */
  search: (query: string, limit?: number) => Promise<StockSearchEntry[]>
}

/** 股票搜索：模块级共享状态 + 本地三路过滤。 */
export function useStockSearch(): UseStockSearch {
  function eagerLoad() {
    if (cachedIndex || loadPromise) return
    loading.value = true
    failed.value = false
    loadError.value = ''
    ensureIndex()
      .then(() => {
        ready.value = true
      })
      .catch((e) => {
        failed.value = true
        loadError.value = formatError(e)
      })
      .finally(() => {
        loading.value = false
      })
  }

  async function search(query: string, limit = 30): Promise<StockSearchEntry[]> {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const index = await ensureIndex()
    const out: StockSearchEntry[] = []
    for (const entry of index) {
      if (matchEntry(entry, q)) {
        out.push(entry)
        if (out.length >= limit) break
      }
    }
    return out
  }

  return { ready, loading, failed, loadError, eagerLoad, search }
}
