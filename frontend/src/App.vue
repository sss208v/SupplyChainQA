<template>
  <div v-if="hasError" class="global-error">
    <div class="global-error-card">
      <h2>⚠️ 应用出现异常</h2>
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
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-thumb { background: var(--color-text-meta); border-radius: 3px; }
::-webkit-scrollbar-track { background: transparent; }

.global-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: #f5f7fa;
}
.global-error-card {
  background: #fff;
  border-radius: 12px;
  padding: 40px;
  text-align: center;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  max-width: 420px;
}
.global-error-card h2 {
  color: #e6a23c;
  margin-bottom: 12px;
}
.global-error-card p {
  color: #909399;
  font-size: 14px;
  margin-bottom: 20px;
  word-break: break-all;
}
</style>
