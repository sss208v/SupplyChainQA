<template>
  <div v-if="hasError" class="global-error">
    <div class="global-error-card">
      <h2>应用出现异常</h2>
      <p>{{ errorMessage }}</p>
      <el-button type="primary" @click="reload">重新加载</el-button>
    </div>
  </div>
  <MainLayout v-else />
</template>

<script setup>
import { ref, onErrorCaptured } from 'vue'
import MainLayout from '@/components/Layout/MainLayout.vue'

const hasError = ref(false)
const errorMessage = ref('')

onErrorCaptured((err) => {
  hasError.value = true
  errorMessage.value = err?.message || String(err)
  console.error('[App] 全局异常捕获:', err)
  return false // 阻止错误继续传播
})

function reload() {
  window.location.reload()
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
html, body, #app {
  height: 100%;
  font-family: var(--font-body);
}
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-thumb { background: var(--color-text-meta); border-radius: 9999px; }
::-webkit-scrollbar-thumb:hover { background: var(--color-text-placeholder); }
::-webkit-scrollbar-track { background: transparent; }

.global-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--color-bg-page);
}
.global-error-card {
  background: var(--color-bg-card);
  border-radius: var(--radius-xl);
  padding: var(--space-10);
  text-align: center;
  box-shadow: var(--shadow-floating);
  border: 1px solid var(--color-border-light);
  max-width: 420px;
}
.global-error-card h2 {
  color: var(--color-warning);
  margin-bottom: 12px;
}
.global-error-card p {
  color: var(--color-text-placeholder);
  font-size: 14px;
  margin-bottom: 20px;
  word-break: break-all;
}
</style>
