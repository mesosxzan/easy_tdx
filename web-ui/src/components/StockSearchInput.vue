<script setup lang="ts">
// 股票搜索输入框：支持 6 位代码 / 中文名 / 拼音声母（如 zjxc→中际旭创）。
// 下拉建议 + 键盘导航（↑↓/Enter/Esc）。v-model 绑定 6 位代码。
// 数据源：useStockSearch composable（模块级缓存索引，整会话拉一次）。

import { computed, nextTick, ref, watch } from 'vue'

import { useStockSearch } from '../composables/useStockSearch'
import { detectMarket, marketLabel } from '../market'
import type { StockSearchEntry } from '../types'

const code = defineModel<string>({ default: '' })

const props = withDefaults(defineProps<{ placeholder?: string }>(), {
  placeholder: '代码 / 拼音 / 名字',
})

const emit = defineEmits<{
  /** 选中某只股票（code + name）时触发，供父组件做额外处理（如回填名称） */
  select: [entry: StockSearchEntry]
  /** 无下拉时按 Enter 触发（输入满 6 位代码的"确认"场景，供组合页接"添加"） */
  confirm: [code: string]
}>()

const { ready, loadError, search } = useStockSearch()

// 输入框文本（可能是代码片段、拼音、中文）。与 code 解耦：
// code 是最终选定的 6 位代码，inputText 是用户正在敲的内容
const inputText = ref(code.value)
const suggestions = ref<StockSearchEntry[]>([])
const showDropdown = ref(false)
const activeIndex = ref(-1) // 键盘高亮项，-1 表示不高亮
const inputRef = ref<HTMLInputElement | null>(null)

// 输入满 6 位纯数字 → 直接当成选定代码（保留"直接敲代码"的老习惯）
const isFullCode = computed(() => /^\d{6}$/.test(inputText.value.trim()))

// 智能识别的市场（用于提示展示）
const detectedMarket = computed(() =>
  code.value && /^\d{6}$/.test(code.value) ? marketLabel(detectMarket(code.value)) : '',
)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

async function refreshSuggestions() {
  const q = inputText.value.trim().toLowerCase()
  // 满 6 位纯数字：清空下拉（已经是有效代码，无需搜索）
  if (/^\d{6}$/.test(q)) {
    suggestions.value = []
    showDropdown.value = false
    activeIndex.value = -1
    return
  }
  if (!q || q.length < 1) {
    suggestions.value = []
    showDropdown.value = false
    activeIndex.value = -1
    return
  }
  if (!ready.value) return // 索引未就绪，等加载完再过滤
  suggestions.value = await search(q, 30)
  showDropdown.value = suggestions.value.length > 0
  activeIndex.value = suggestions.value.length > 0 ? 0 : -1
}

watch(inputText, () => {
  // 同步纯数字输入到 code（边敲代码边更新市场标签）
  if (/^\d{6}$/.test(inputText.value.trim())) {
    code.value = inputText.value.trim()
  }
  // 防抖 120ms
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(refreshSuggestions, 120)
})

function selectEntry(entry: StockSearchEntry) {
  inputText.value = entry.code
  code.value = entry.code
  suggestions.value = []
  showDropdown.value = false
  activeIndex.value = -1
  emit('select', entry)
  inputRef.value?.focus()
}

function onKeydown(e: KeyboardEvent) {
  if (!showDropdown.value || suggestions.value.length === 0) {
    // 无下拉时，Enter 且输入是有效代码 → 通知父组件"确认"（如组合页添加标的）
    if (e.key === 'Enter' && isFullCode.value) {
      emit('confirm', code.value)
    }
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeIndex.value = (activeIndex.value + 1) % suggestions.value.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIndex.value =
      (activeIndex.value - 1 + suggestions.value.length) % suggestions.value.length
  } else if (e.key === 'Enter') {
    if (activeIndex.value >= 0 && activeIndex.value < suggestions.value.length) {
      e.preventDefault()
      selectEntry(suggestions.value[activeIndex.value])
    }
  } else if (e.key === 'Escape') {
    showDropdown.value = false
    activeIndex.value = -1
  }
}

function onBlur() {
  // 延迟关闭，给 click 事件时间触发（mousedown 在 blur 前，但 click 在后）
  setTimeout(() => {
    showDropdown.value = false
  }, 150)
}

function onFocus() {
  // 索引由 App 根挂载时 eagerLoad 触发（详见 App.vue），这里聚焦时无需再触发。
  // 聚焦时若已有输入且非完整代码，重新展示建议
  nextTick(() => {
    if (inputText.value.trim() && !isFullCode.value && suggestions.value.length > 0) {
      showDropdown.value = true
    }
  })
}

// 父组件外部更新 code 时（如 URL 回填），同步到输入框
watch(code, (newCode) => {
  if (newCode !== inputText.value) {
    inputText.value = newCode
  }
})
</script>

<template>
  <div class="stock-search-input">
    <input
      ref="inputRef"
      v-model="inputText"
      type="text"
      autocomplete="off"
      :placeholder="props.placeholder"
      @keydown="onKeydown"
      @blur="onBlur"
      @focus="onFocus"
    />
    <span v-if="detectedMarket" class="market-tag">{{ detectedMarket }}</span>
    <span v-if="loadError" class="load-err" :title="loadError">⚠</span>

    <ul v-if="showDropdown" class="suggestions">
      <li
        v-for="(s, i) in suggestions"
        :key="s.code"
        :class="{ active: i === activeIndex }"
        @mousedown.prevent="selectEntry(s)"
        @mouseenter="activeIndex = i"
      >
        <span class="code">{{ s.code }}</span>
        <span class="name">{{ s.name }}</span>
        <span v-if="s.initials" class="initials">{{ s.initials }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.stock-search-input {
  position: relative;
  width: 100%;
}
.stock-search-input input {
  width: 100%;
  padding-right: 70px;
}
.market-tag {
  position: absolute;
  right: 8px;
  bottom: 8px;
  font-size: 11px;
  color: var(--text-dim);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 3px;
}
.load-err {
  position: absolute;
  right: 8px;
  top: 8px;
  color: var(--up);
  font-size: 14px;
}

.suggestions {
  position: absolute;
  z-index: 100;
  top: calc(100% + 2px);
  left: 0;
  right: 0;
  max-height: 320px;
  overflow-y: auto;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  list-style: none;
  margin: 0;
  padding: 0;
}
.suggestions li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  cursor: pointer;
  font-size: 13px;
}
.suggestions li:hover,
.suggestions li.active {
  background: var(--bg-elevated);
}
.suggestions .code {
  font-family: var(--font-mono);
  color: var(--text-dim);
  width: 64px;
  flex-shrink: 0;
}
.suggestions .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suggestions .initials {
  font-size: 11px;
  color: var(--text-dim);
  opacity: 0.7;
  text-transform: lowercase;
}
</style>
