// 后端 API 封装。统一 fetch + 错误处理，返回类型化结果。
// 开发期通过 vite proxy 走 /api（同源），生产期由 FastAPI 同源托管。

import type {
  ApiError,
  BacktestRequest,
  BacktestResult,
  Bar,
  Category,
  OptimizeBacktestRequest,
  PortfolioBacktestRequest,
  TaskListResponse,
  StrategiesResponse,
  TaskState,
  TaskSubmitResponse,
} from './types'

const BASE = '/api/v1'

/** 把未知错误格式化为用户可读的消息（网络错误给友好提示）。 */
export function formatError(e: unknown): string {
  if (e instanceof TypeError && e.message.includes('fetch')) {
    return '网络错误：无法连接后端服务，请确认 easy-tdx serve 已启动'
  }
  return e instanceof Error ? e.message : String(e)
}

/** 把 Response 解析为 ApiError 抛出（后端统一错误格式 {error, detail}）。 */
async function throwError(resp: Response): Promise<never> {
  let detail = `${resp.status} ${resp.statusText}`
  try {
    const body = (await resp.json()) as ApiError
    if (body?.detail) detail = body.detail
  } catch {
    // 非 JSON 错误体，用 statusText
  }
  throw new Error(detail)
}

/** 枚举预置策略 + 参数 schema。 */
export async function fetchStrategies(): Promise<StrategiesResponse> {
  const resp = await fetch(`${BASE}/backtest/strategies`)
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as StrategiesResponse
}

/**
 * 按标的取 K 线行情（OHLCV）。
 *
 * 后端 /bars 仅支持按 count 取数（上限 800 根，约 3.2 年日线），不支持日期范围。
 * 这里固定拉满 800 根，由调用方按日期范围在前端过滤。
 * 可选 startDate/endDate 对结果做闭区间过滤（ISO 日期字符串，如 "2024-01-01"）。
 */
export async function fetchBars(
  market: string,
  code: string,
  category: Category,
  startDate?: string,
  endDate?: string,
): Promise<Bar[]> {
  const params = new URLSearchParams({
    market,
    code,
    category,
    count: '800', // 后端硬上限，前端按日期过滤
  })
  const resp = await fetch(`${BASE}/bars?${params}`)
  if (!resp.ok) await throwError(resp)
  const body = (await resp.json()) as { data: Record<string, unknown>[] }
  // 后端 bars 列名不统一：日线及以上是 `date`，分钟线是 `datetime`。
  // 归一化为统一 `datetime` 字段（取 ISO 前 19 位）。
  let bars = body.data.map((row) => normalizeBar(row))
  // 按日期范围过滤（闭区间，比较日期部分 YYYY-MM-DD）
  if (startDate) bars = bars.filter((b) => b.datetime.slice(0, 10) >= startDate)
  if (endDate) bars = bars.filter((b) => b.datetime.slice(0, 10) <= endDate)
  return bars
}

/** 把后端 bars 的单条记录归一化为统一 Bar（datetime 字段）。 */
function normalizeBar(row: Record<string, unknown>): Bar {
  const raw = (row.datetime ?? row.date) as string | undefined
  if (!raw) throw new Error('行情数据缺少 datetime/date 字段')
  return {
    datetime: raw.slice(0, 19).replace(' ', 'T'),
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
    vol: Number(row.vol),
    amount: Number(row.amount),
  }
}

/** 同步回测（内联 OHLCV，快速）。 */
export async function runBacktest(req: BacktestRequest): Promise<BacktestResult> {
  const resp = await fetch(`${BASE}/backtest/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as BacktestResult
}

/** 提交后台回测任务，返回 task_id。 */
export async function submitBacktestTask(req: BacktestRequest): Promise<TaskSubmitResponse> {
  const resp = await fetch(`${BASE}/backtest/run/async`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as TaskSubmitResponse
}

/** 提交组合回测后台任务，返回 task_id。 */
export async function submitPortfolioTask(
  req: PortfolioBacktestRequest,
): Promise<TaskSubmitResponse> {
  const resp = await fetch(`${BASE}/backtest/portfolio/run/async`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as TaskSubmitResponse
}

/** 提交参数网格寻优后台任务，返回 task_id。 */
export async function submitOptimizeTask(
  req: OptimizeBacktestRequest,
): Promise<TaskSubmitResponse> {
  const resp = await fetch(`${BASE}/backtest/optimize/run/async`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as TaskSubmitResponse
}

/** 查询后台任务状态（轮询用）。 */
export async function fetchTask(taskId: string): Promise<TaskState> {
  const resp = await fetch(`${BASE}/backtest/tasks/${taskId}`)
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as TaskState
}

/** 列出最近任务摘要（供对比页选择）。 */
export async function fetchTaskList(limit = 20): Promise<TaskListResponse> {
  const resp = await fetch(`${BASE}/backtest/tasks?limit=${limit}`)
  if (!resp.ok) await throwError(resp)
  return (await resp.json()) as TaskListResponse
}

/**
 * 提交后台任务并轮询直到 done/failed。
 * @param req 回测请求
 * @param onPoll 每次轮询回调（可选，用于更新 UI 进度）
 * @param intervalMs 轮询间隔（默认 300ms）
 * @param timeoutMs 总超时（默认 120s）
 */
export async function runBacktestWithPolling(
  req: BacktestRequest,
  onPoll?: (state: TaskState) => void,
  intervalMs = 300,
  timeoutMs = 120_000,
): Promise<TaskState> {
  const { task_id } = await submitBacktestTask(req)
  const start = Date.now()
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const state = await fetchTask(task_id)
    onPoll?.(state)
    if (state.status === 'done' || state.status === 'failed') return state
    if (Date.now() - start > timeoutMs) {
      throw new Error(`回测任务超时（${timeoutMs / 1000}s）`)
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
