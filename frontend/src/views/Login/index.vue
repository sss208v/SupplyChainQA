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

const demoAccounts = [
  { label: '管理员', username: 'admin', password: 'admin123' },
  { label: '采购部', username: 'purchase', password: 'purchase123' },
  { label: '仓库部', username: 'warehouse', password: 'warehouse123' },
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
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.login-card {
  width: 400px;
  padding: var(--space-10);
  background: var(--color-bg-card);
  border-radius: 16px;
  box-shadow: var(--shadow-brand);
}

.login-header {
  text-align: center;
  margin-bottom: var(--space-8);
}

.login-header h1 {
  font-size: 26px;
  font-weight: 800;
  color: var(--color-text-primary);
  margin-bottom: 4px;
}

.login-header p {
  font-size: 13px;
  color: var(--color-text-placeholder);
}

.demo-accounts {
  margin-top: var(--space-6);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.demo-title {
  font-size: 12px;
  color: var(--color-text-placeholder);
  margin-bottom: var(--space-2);
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
  transition: transform 0.2s ease;
}

.demo-tag:hover {
  transform: scale(1.03);
}
</style>
