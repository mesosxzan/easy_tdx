<script setup lang="ts">
// 结果对比页面：选 2-4 个已完成的回测 task，叠加净值曲线 + 横向指标对比。

import { onMounted, ref, watch } from 'vue'

import CompareChart from '../components/CompareChart.vue'
import CompareTable from '../components/CompareTable.vue'
import { fetchTask, fetchTaskList, formatError } from '../api'
import type { BacktestResult, TaskSummary } from '../types'

const taskList = ref<TaskSummary[]>([])
const loading = ref(false)
const error = ref('')
const selectedIds = ref<Set<string>>(new Set())
// 已加载的详情（task_id → BacktestResult）
const details = ref<Map<string, { label: string; result: BacktestResult }>>(new Map())

onMounted(loadTasks)

async function loadTasks() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetchTaskList(20)
    // 只显示已完成的单标的回测（status=done 且 result 是 BacktestResult）
    taskList.value = resp.tasks.filter((t) => t.status === 'done')
  } catch (e) {
    error.value = formatError(e)
  } finally {
    loading.value = false
  }
}

async function toggle(taskId: string) {
  if (selectedIds.value.has(taskId)) {
    selectedIds.value.delete(taskId)
    details.value.delete(taskId)
  } else {
    if (selectedIds.value.size >= 4) return // 最多 4 个
    selectedIds.value.add(taskId)
    // 拉详情
    try {
      const state = await fetchTask(taskId)
      const result = state.result as BacktestResult
      if (!result?.performance || !result?.equity_curve) {
        throw new Error('该任务结果不可对比（非单标的回测）')
      }
      details.value.set(taskId, { label: state.description, result })
    } catch (e) {
      selectedIds.value.delete(taskId)
      error.value = e instanceof Error ? e.message : String(e)
    }
  }
  // 触发响应式
  selectedIds.value = new Set(selectedIds.value)
  details.value = new Map(details.value)
}

const compareItems = ref<Array<{ label: string; result: BacktestResult }>>([])
function refreshItems() {
  compareItems.value = Array.from(details.value.values())
}

// 监听 details 变化刷新对比项
watch(details, refreshItems, { deep: true })
</script>

<template>
  <div class="compare-view">
    <aside class="config-panel">
      <section class="panel-section">
        <h3>选择对比任务</h3>
        <button class="refresh-btn" :disabled="loading" @click="loadTasks">
          {{ loading ? '加载中…' : '刷新列表' }}
        </button>
        <p class="hint">勾选 2-4 个已完成的回测任务进行对比：</p>
        <div v-if="taskList.length === 0" class="empty">暂无已完成的任务</div>
        <div v-for="t in taskList" :key="t.task_id" class="task-item">
          <label class="check">
            <input
              type="checkbox"
              :checked="selectedIds.has(t.task_id)"
              :disabled="!selectedIds.has(t.task_id) && selectedIds.size >= 4"
              @change="toggle(t.task_id)"
            />
            <span class="task-desc">{{ t.description || t.task_id.slice(0, 8) }}</span>
          </label>
          <span class="task-meta">{{ t.elapsed.toFixed(1) }}s</span>
        </div>
      </section>
    </aside>

    <main class="report-panel">
      <div v-if="error" class="error-banner">⚠ {{ error }}</div>

      <div v-if="compareItems.length < 2" class="placeholder">
        <p>勾选至少 2 个任务进行对比</p>
      </div>

      <div v-else class="report-content">
        <section class="report-section">
          <h3>净值曲线对比（归一化）</h3>
          <CompareChart :items="compareItems" />
        </section>

        <section class="report-section">
          <h3>指标对比</h3>
          <CompareTable :items="compareItems" />
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.compare-view {
  display: flex;
  height: 100%;
}
.config-panel {
  width: 320px;
  flex-shrink: 0;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  padding: 16px;
  overflow-y: auto;
}
.panel-section h3 {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
}
.refresh-btn {
  width: 100%;
  margin-bottom: 12px;
}
.hint {
  color: var(--text-muted);
  font-size: 12px;
  margin-bottom: 10px;
}
.empty {
  color: var(--text-dim);
  font-size: 13px;
  padding: 16px 0;
}
.task-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
.check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.check input[type='checkbox'] {
  width: auto;
}
.task-desc {
  color: var(--text);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.task-meta {
  color: var(--text-dim);
  font-size: 11px;
  flex-shrink: 0;
}
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
</style>
