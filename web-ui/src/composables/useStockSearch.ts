// 股票搜索 composable：模块级缓存搜索索引 + 按代码/名字/声母三路过滤。
// 索引整会话只拉一次（~150KB / 5000 条），后续过滤纯本地计算（<5ms）。
//
// ⚠️ 重要：索引构建是"按需触发"——只在用户首次聚焦搜索输入框时才拉取，
// 不在组件挂载时自动拉。原因是后端首次构建索引要走全量 get_security_list_all
// （几十次 TDX 协议往返，几十秒），而 AsyncTdxClient 是单连接 + _execute_lock，
// 索引构建期间会独占连接、阻塞所有 /bars 行情请求。按需触发确保"打开页面
// 直接点取行情/寻优"的核心路径不被搜索索引拖累。

import { ref } from 'vue'

import { fetchSearchIndex, formatError } from '../api'
import type { StockSearchEntry } from '../types'

// ── 模块级缓存（所有组件实例共享一次拉取） ─────────────────────────────────
let cachedIndex: StockSearchEntry[] | null = null
let loadPromise: Promise<StockSearchEntry[]> | null = null

/** 三路匹配：代码前缀 / 名字包含 / 声母包含。
 *  query 纯数字时优先按代码前缀（照顾"直接输 6 位代码"的老习惯）；
 *  含字母时按声母；任何情况都叠加名字包含（输"旭创"也能命中）。 */
function matchEntry(entry: StockSearchEntry, q: string): boolean {
  if (entry.code.startsWith(q)) return true
  if (entry.name.includes(q)) return true
  if (entry.initials.includes(q)) return true
  return false
}

/** 拉索引（去重并发请求；成功后常驻模块级缓存）。失败抛错，调用方处理。 */
async function ensureIndex(): Promise<StockSearchEntry[]> {
  if (cachedIndex) return cachedIndex
  if (!loadPromise) {
    loadPromise = (async () => {
      const { data } = await fetchSearchIndex()
      cachedIndex = data
      return data
    })().catch((e) => {
      // 失败清空 promise，允许下次重试
      loadPromise = null
      throw e
    })
  }
  return loadPromise
}

export interface UseStockSearch {
  /** 索引是否已加载就绪 */
  ready: ReturnType<typeof ref<boolean>>
  /** 加载错误信息（空串表示无错；搜索不可用时静默降级，不阻塞主流程） */
  loadError: ReturnType<typeof ref<string>>
  /** 显式触发索引加载（首次聚焦输入框时调用）。静默失败，不抛错。 */
  ensureLoaded: () => void
  /** 按输入过滤，返回最多 limit 条（默认 30）。索引未就绪时返回空数组。 */
  search: (query: string, limit?: number) => Promise<StockSearchEntry[]>
}

/** 股票搜索：按需加载索引 + 本地三路过滤。 */
export function useStockSearch(): UseStockSearch {
  const ready = ref(false)
  const loadError = ref('')

  function ensureLoaded() {
    if (cachedIndex || loadPromise) return
    ensureIndex()
      .then(() => {
        ready.value = true
      })
      .catch((e) => {
        // 静默失败：搜索是附加功能，不能因为索引拉不到而打扰用户。
        // 只记 loadError 供输入框显示一个小 ⚠ 图标，不弹错、不阻塞。
        loadError.value = formatError(e)
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

  return { ready, loadError, ensureLoaded, search }
}
