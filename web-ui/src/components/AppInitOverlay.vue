<script setup lang="ts">
// 全局初始化遮罩：股票搜索索引加载期间盖住全站，给用户明确的初始化反馈。
// App 根挂载时调 eagerLoad()，本组件监听 loading/ready/failed 三态：
//   - loading：全屏灰底 + spinner + "正在初始化股票列表…"
//   - ready / failed：fade-out 消失（失败也消失，避免卡死；搜索降级为只能输代码）
//
// 方案 A：无"跳过"按钮，强制等待（索引构建是一次性代价，之后整会话秒回）。
// 唯一的"逃逸阀"是加载失败——失败时遮罩消失，搜索不可用但不阻塞其他功能。

import { useStockSearch } from '../composables/useStockSearch'

const { loading, ready, failed, loadError } = useStockSearch()

// 是否显示遮罩：加载中显示；就绪或失败后淡出
// ready 已就绪过的会话（缓存命中）loading 不会变 true，遮罩根本不显示
function shouldShow() {
  return loading.value && !ready.value
}
</script>

<template>
  <Transition name="overlay-fade">
    <div v-if="shouldShow()" class="app-init-overlay">
      <div class="init-card">
        <div class="spinner" />
        <h2>正在初始化股票列表…</h2>
        <p class="sub">首次加载需从通达信服务器拉取沪深 A 股全名单（约 5000 只），</p>
        <p class="sub">预计 30-60 秒，请稍候。</p>
        <p class="hint">完成后即可使用拼音声母搜索（如 zjxc → 中际旭创）</p>
      </div>
    </div>
  </Transition>
  <!-- 失败提示条（非阻塞，顶部小横幅） -->
  <Transition name="banner-slide">
    <div v-if="failed" class="init-failed-banner">
      ⚠ 股票搜索初始化失败：{{ loadError }}。拼音搜索不可用，但仍可手动输入 6 位代码。
    </div>
  </Transition>
</template>

<style scoped>
.app-init-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 17, 23, 0.88);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}

.init-card {
  text-align: center;
  padding: 40px 48px;
  max-width: 440px;
}

.spinner {
  width: 48px;
  height: 48px;
  margin: 0 auto 24px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.init-card h2 {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 12px;
}

.sub {
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.6;
  margin: 2px 0;
}

.hint {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

/* 淡出过渡 */
.overlay-fade-leave-active {
  transition: opacity 0.4s ease;
}
.overlay-fade-leave-to {
  opacity: 0;
}

/* 失败横幅 */
.init-failed-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9998;
  padding: 10px 20px;
  background: rgba(239, 65, 70, 0.12);
  border-bottom: 1px solid var(--up);
  color: var(--up);
  font-size: 13px;
  text-align: center;
}

.banner-slide-enter-active,
.banner-slide-leave-active {
  transition: all 0.3s ease;
}
.banner-slide-enter-from,
.banner-slide-leave-to {
  transform: translateY(-100%);
  opacity: 0;
}
</style>
