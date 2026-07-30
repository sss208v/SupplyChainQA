<template>
  <!-- 登录页：不显示布局 -->
  <router-view v-if="route.path === '/login'" />

  <!-- 已登录：显示完整布局 -->
  <el-container v-else class="main-layout">
    <!-- 侧边栏 -->
        <!-- Mobile overlay -->
    <div v-if="isMobile && mobileMenuOpen" class="mobile-overlay" @click="mobileMenuOpen = false" />
    <el-aside
      :class="{ 'mobile-sidebar': isMobile, 'mobile-sidebar-open': isMobile && mobileMenuOpen }"
      :width="isCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo">
        <el-icon :size="28"><Monitor /></el-icon>
        <span v-show="!isCollapsed" class="logo-text">供应链助手</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        router
        class="sidebar-menu"
        background-color="transparent"
        :text-color="'#9ca3af'"
        :active-text-color="'#60a5fa'"
      >
                <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <template #title>系统概览</template>
        </el-menu-item>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>智能对话</template>
        </el-menu-item>

        <el-menu-item index="/knowledge">
          <el-icon><Folder /></el-icon>
          <template #title>知识库管理</template>
        </el-menu-item>

        <el-menu-item index="/tools">
          <el-icon><SetUp /></el-icon>
          <template #title>工具管理</template>
        </el-menu-item>

        <el-menu-item index="/evaluate">
          <el-icon><DataAnalysis /></el-icon>
          <template #title>RAG 评估</template>
        </el-menu-item>
      </el-menu>
      <!-- 最近对话历史 -->
      <div v-show="!isCollapsed" class="history-section">
        <el-divider class="history-divider">
          <span class="history-divider-text">最近对话</span>
        </el-divider>
        <div class="history-list">
          <div
            v-for="entry in chatHistory"
            :key="entry.sessionId"
            class="history-item"
            @click="handleHistoryClick(entry)"
          >
            <div class="history-item-main">
              <span class="history-item-query">{{ truncate(entry.lastQuery, 16) }}</span>
              <span class="history-item-id">{{ entry.sessionId.slice(0, 8) }}</span>
            </div>
            <span class="history-item-time">{{ formatTime(entry.timestamp) }}</span>
          </div>
          <div v-if="chatHistory.length === 0" class="history-empty">
            暂无对话记录
          </div>
        </div>
      </div>

      <div class="sidebar-footer">
        <el-button
          text
          :icon="isCollapsed ? Expand : Fold"
          @click="isCollapsed = !isCollapsed"
          class="collapse-btn"
        />
        <el-tooltip content="系统架构图" placement="right">
          <el-button
            text
            :icon="Link"
            class="arch-btn"
            @click="openArchitecture"
          />
        </el-tooltip>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-container class="main-content">
      <el-header class="header">
        <div class="header-left">
          <el-button
            v-if="isMobile"
            :icon="IconMenu"
            text
            class="mobile-menu-btn"
            @click="mobileMenuOpen = !mobileMenuOpen"
          />
          <h3>{{ currentPageTitle }}</h3>
        </div>
        <div class="header-right">
          <!-- 模型切换 -->
          <el-select
            v-model="currentModel"
            size="small"
            style="width: 140px; margin-right: 12px"
            @change="onModelChange"
          >
            <el-option label="llama.cpp (本地)" value="llamacpp" />
            <el-option label="Ollama" value="ollama" />
          </el-select>
          <el-tag
            :type="backendOnline ? 'success' : 'danger'"
            effect="plain"
            round
            size="small"
          >
            <el-icon><CircleCheck v-if="backendOnline" /><WarningFilled v-else /></el-icon>
            {{ backendOnline ? '在线' : '后端离线' }}
          </el-tag>
          <!-- 用户信息 -->
          <el-dropdown @command="handleCommand" class="user-dropdown">
            <span class="user-info">
              <el-icon><User /></el-icon>
              <span class="username">{{ authStore.username }}</span>
              <el-tag
                v-if="deptLabel"
                size="small"
                effect="plain"
                round
                class="dept-tag"
              >
                {{ deptLabel }}
              </el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
/**
 * Supply Chain QA - 主布局组件
 *
 * 1. Element Plus 的 Container 布局：el-container + el-aside + el-header + el-main
 * 2. 侧边栏折叠：通过 isCollapsed 控制 el-aside 宽度和 el-menu 的 collapse 属性
 * 3. 路由菜单：el-menu 的 router 属性让菜单项点击自动跳转
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Expand, Fold, ChatDotRound, Folder, SetUp, Monitor, CircleCheck, DataAnalysis, User, Link, WarningFilled, Odometer, Menu as IconMenu } from '@element-plus/icons-vue'
import { listModels, switchModel } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isCollapsed = ref(false)
const mobileMenuOpen = ref(false)
const isMobile = ref(window.innerWidth < 768)
const currentModel = ref('llamacpp')
const backendOnline = ref(true)

// ---- 对话历史 ----
const HISTORY_KEY = 'scqa_chat_history'
const MAX_HISTORY = 20
const chatHistory = ref([])

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    chatHistory.value = raw ? JSON.parse(raw) : []
  } catch {
    chatHistory.value = []
  }
}

function saveHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory.value))
}

/**
 * 添加一条对话历史记录（供外部调用）
 */
function addHistoryEntry(sessionId, lastQuery) {
  if (!sessionId) return
  // 去重：如果已有相同sessionId，移除旧的
  chatHistory.value = chatHistory.value.filter(h => h.sessionId !== sessionId)
  chatHistory.value.unshift({
    sessionId,
    lastQuery: lastQuery || '',
    timestamp: Date.now(),
  })
  // 限制最多 MAX_HISTORY 条
  if (chatHistory.value.length > MAX_HISTORY) {
    chatHistory.value = chatHistory.value.slice(0, MAX_HISTORY)
  }
  saveHistory()
}

/**
 * 点击历史记录条目
 */
function handleHistoryClick(entry) {
  router.push({ path: '/chat', query: { session: entry.sessionId } })
}

/**
 * 格式化时间戳
 */
function formatTime(ts) {
  const d = new Date(ts)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (isToday) return time
  const month = d.getMonth() + 1
  const day = d.getDate()
  return `${month}/${day} ${time}`
}

/**
 * 截断文本
 */
function truncate(str, len) {
  if (!str) return ''
  return str.length > len ? str.slice(0, len) + '...' : str
}

// 将 addHistoryEntry 挂到 window 上，方便 Chat 模块调用
onMounted(() => {
  window.addEventListener('resize', () => {
    isMobile.value = window.innerWidth < 768
    if (!isMobile.value) mobileMenuOpen.value = false
  })
  if (isMobile.value) isCollapsed.value = true

  loadHistory()
  window.__scqa_addHistory = addHistoryEntry

  // 健康检查轮询（每 30 秒）
  async function checkHealth() {
    try {
      const res = await fetch('/health')
      backendOnline.value = res.ok
    } catch {
      backendOnline.value = false
    }
  }
  checkHealth()
  setInterval(checkHealth, 30000)
})

// 初始化时获取当前模型
listModels().then(res => {
  currentModel.value = res.current
}).catch(() => {})

const activeMenu = computed(() => route.path)
const currentPageTitle = computed(() => route.meta.title || '供应链助手')

// 部门标签映射
const deptLabels = {
  admin: '管理员',
  purchase: '采购部',
  warehouse: '仓库部',
  quality: '质量部',
  production: '生产部',
  finance: '财务部',
  logistics: '物流部',
}
const deptLabel = computed(() => deptLabels[authStore.role] || authStore.department || '')

async function onModelChange(provider) {
  try {
    const res = await switchModel(provider)
    ElMessage.success(res.message)
  } catch (e) {
    ElMessage.error(e.message || '切换失败')
    currentModel.value = 'llamacpp'
  }
}

function handleCommand(command) {
  if (command === 'logout') {
    authStore.logout()
    router.push('/login')
    ElMessage.success('已退出登录')
  }
}

function openArchitecture() {
  const url = `${window.location.origin}/architecture.html`
  window.open(url, '_blank')
}
</script>

<style scoped>
.main-layout {
  height: 100vh;
}

/* ===== Sidebar ===== */
.sidebar {
  background: linear-gradient(180deg, var(--color-sidebar-bg) 0%, #0f1420 100%);
  transition: width 0.3s var(--transition-slow);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--color-sidebar-text);
  --el-menu-active-color: var(--color-sidebar-active);
  --el-menu-hover-bg-color: var(--color-sidebar-hover);
}

.sidebar-menu :deep(.el-menu-item) {
  height: 44px;
  line-height: 44px;
  margin: 2px 8px;
  border-radius: var(--radius-md);
  transition: all var(--transition-base);
}

.sidebar-menu :deep(.el-menu-item:hover) {
  transform: translateX(2px);
}

.sidebar-menu :deep(.el-menu-item.is-active) {
  background: rgba(37, 99, 235, 0.12) !important;
  color: #60a5fa !important;
  font-weight: 500;
}

.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #f9fafb;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  border-bottom: 1px solid var(--color-sidebar-border);
  flex-shrink: 0;
}

.logo :deep(.el-icon) {
  color: var(--color-primary);
}

.logo-text {
  white-space: nowrap;
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid var(--color-sidebar-border);
  text-align: center;
}

.collapse-btn {
  color: var(--color-sidebar-text) !important;
}

.arch-btn {
  color: var(--color-sidebar-text) !important;
  margin-left: 4px;
}

.arch-btn:hover {
  color: var(--color-primary) !important;
}

/* ===== History Section ===== */
.history-section {
  flex-shrink: 0;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.history-divider {
  margin: 8px 16px;
  border-color: var(--color-sidebar-border);
}

.history-divider-text {
  color: var(--color-sidebar-meta);
  font-size: 11px;
  letter-spacing: 1px;
}

.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 4px;
}

.history-list::-webkit-scrollbar {
  width: 4px;
}

.history-list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  margin-bottom: 2px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-base);
}

.history-item:hover {
  background: var(--color-sidebar-hover);
  transform: translateX(2px);
}

.history-item-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.history-item-query {
  color: #c0c4cc;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-item-id {
  color: var(--color-sidebar-meta);
  font-size: 10px;
  font-family: var(--font-mono);
  margin-top: 2px;
}

.history-item-time {
  color: var(--color-sidebar-meta);
  font-size: 10px;
  flex-shrink: 0;
  margin-left: 8px;
}

.history-empty {
  color: var(--color-sidebar-meta);
  font-size: 12px;
  text-align: center;
  padding: 12px 0;
}

/* ===== Main Content ===== */
.main-content {
  background: var(--color-bg-page);
}

.header {
  height: 60px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--color-border-light);
  padding: 0 var(--space-6);
  z-index: 100;
}

.header-left h3 {
  font-family: var(--font-heading);
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
  margin: 0;
}

.content {
  padding: var(--space-6);
  overflow: auto;
  background: var(--color-bg-page);
}

.user-dropdown {
  margin-left: 16px;
}

.user-dropdown :deep(.el-tooltip__trigger:focus-visible) {
  outline: none;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
  color: var(--color-text-body);
  transition: color var(--transition-fast);
  border-radius: var(--radius-md);
  padding: 4px 8px;
  margin: -4px -8px;
}

.user-info:hover {
  color: var(--color-primary);
  background: var(--color-bg-hover);
}

.username {
  font-weight: 500;
}

.dept-tag {
  margin-left: 4px;
}

/* ===== Mobile Responsive ===== */
@media (max-width: 767px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 1001;
    width: 260px !important;
    transform: translateX(-100%);
    transition: transform 0.3s ease;
  }

  .mobile-sidebar-open {
    transform: translateX(0) !important;
  }

  .mobile-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 1000;
  }

  .mobile-menu-btn {
    margin-right: 8px;
    font-size: 22px;
    color: var(--color-text-body);
    flex-shrink: 0;
  }

  .header {
    padding: 0 12px !important;
    height: 48px !important;
  }

  .header-left h3 {
    font-size: 14px !important;
  }

  .header-right {
    gap: 6px;
  }

  .header-right .el-select {
    width: 100px !important;
    margin-right: 4px !important;
  }

  .user-info .username {
    display: none;
  }

  .dept-tag {
    display: none;
  }

  .content {
    padding: 12px !important;
  }

  .history-section {
    max-height: 200px;
  }
}

/* Tablet */
@media (min-width: 768px) and (max-width: 1023px) {
  .header {
    padding: 0 16px !important;
  }

  .content {
    padding: 16px !important;
  }
}
</style>
