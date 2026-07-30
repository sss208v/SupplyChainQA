<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <h1>供应链智能助手</h1>
        <p>Supply Chain QA System</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin">
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            placeholder="用户名"
            prefix-icon="User"
            size="large"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            prefix-icon="Lock"
            size="large"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            style="width: 100%"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>

      <div class="demo-accounts">
        <div class="demo-title">演示账号</div>
        <div class="demo-list">
          <el-tag
            v-for="account in demoAccounts"
            :key="account.username"
            class="demo-tag"
            @click="quickLogin(account)"
          >
            {{ account.label }}
          </el-tag>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)
const form = ref({ username: '', password: '' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

// 演示用预设账号（仅面试演示环境使用，非真实凭证）
const demoAccounts = [
  { label: '管理员', username: 'admin', password: 'admin123' },
  { label: '采购部', username: 'purchase', password: 'purchase123' },
  { label: '仓库部', username: 'warehouse', password: 'warehouse123' },
  { label: '质量部', username: 'quality', password: 'quality123' },
  { label: '生产部', username: 'production', password: 'production123' },
  { label: '财务部', username: 'finance', password: 'finance123' },
  { label: '物流部', username: 'logistics', password: 'logistics123' },
]

async function handleLogin() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    await authStore.login(form.value.username, form.value.password)
    ElMessage.success(`欢迎回来，${authStore.username}`)
    router.push('/chat')
  } catch (err) {
    ElMessage.error(err.message || '登录失败')
  } finally {
    loading.value = false
  }
}

function quickLogin(account) {
  form.value.username = account.username
  form.value.password = account.password
  handleLogin()
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-warm);
  background-image:
    radial-gradient(ellipse at 20% 50%, rgba(37, 99, 235, 0.04) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(99, 102, 241, 0.03) 0%, transparent 40%);
  animation: fadeIn 0.5s ease;
}

.login-card {
  width: 420px;
  padding: var(--space-10) var(--space-8);
  background: var(--color-bg-card);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-floating);
  border: 1px solid var(--color-border-light);
  animation: scaleIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}

.login-header {
  text-align: center;
  margin-bottom: var(--space-8);
}

.login-header h1 {
  font-family: var(--font-heading);
  font-size: 28px;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.03em;
  margin-bottom: 6px;
}

.login-header p {
  font-size: 14px;
  color: var(--color-text-placeholder);
  letter-spacing: 0.02em;
}

.demo-accounts {
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px solid var(--color-border-light);
}

.demo-title {
  font-size: 12px;
  color: var(--color-text-placeholder);
  margin-bottom: var(--space-3);
  text-align: center;
}

.demo-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: center;
}

.demo-tag {
  cursor: pointer;
  font-size: 12px;
  transition: all var(--transition-fast);
  border-color: var(--color-border) !important;
}

.demo-tag:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-raised);
  border-color: var(--color-primary) !important;
  color: var(--color-primary);
}

:deep(.el-button--primary) {
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--radius-md);
  letter-spacing: 0.01em;
  transition: all var(--transition-base);
}

:deep(.el-button--primary:hover) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-card-hover);
}

:deep(.el-button--primary:active) {
  transform: translateY(0);
}

@media (max-width: 767px) {
  .login-card {
    width: 100% !important;
    max-width: 360px !important;
    padding: var(--space-8) var(--space-5) !important;
  }
  .login-header h1 {
    font-size: 22px !important;
  }
}
</style>
