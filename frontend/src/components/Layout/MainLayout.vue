<template>
  <el-container class="main-layout">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo">
        <el-icon :size="28"><Monitor /></el-icon>
        <span v-show="!isCollapsed" class="logo-text">供应链助手</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        router
        class="sidebar-menu"
        background-color="#1d1e2c"
        text-color="#a0a3bd"
        active-text-color="#409eff"
      >
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
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-container class="main-content">
      <el-header class="header">
        <div class="header-left">
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
            <el-option label="DeepSeek" value="deepseek" />
            <el-option label="MiniMax" value="minimax" />
            <el-option label="Ollama" value="ollama" />
          </el-select>
          <el-tag type="success" effect="dark" round>
            <el-icon><CircleCheck /></el-icon> 服务在线
          </el-tag>
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
 * SmartQA Pro - 主布局组件
 *
 * 【学习要点】
 * 1. Element Plus 的 Container 布局：el-container + el-aside + el-header + el-main
 * 2. 侧边栏折叠：通过 isCollapsed 控制 el-aside 宽度和 el-menu 的 collapse 属性
 * 3. 路由菜单：el-menu 的 router 属性让菜单项点击自动跳转
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Expand, Fold, ChatDotRound, Folder, SetUp, Monitor, CircleCheck, DataAnalysis } from '@element-plus/icons-vue'
import { listModels, switchModel } from '@/api/chat'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const isCollapsed = ref(false)
const currentModel = ref('deepseek')

// ---- 对话历史 ----
const HISTORY_KEY = 'smartqa_chat_history'
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
  loadHistory()
  window.__smartqa_addHistory = addHistoryEntry
})

// 初始化时获取当前模型
listModels().then(res => {
  currentModel.value = res.current
}).catch(() => {})

const activeMenu = computed(() => route.path)
const currentPageTitle = computed(() => route.meta.title || '供应链助手')

async function onModelChange(provider) {
  try {
    const res = await switchModel(provider)
    ElMessage.success(res.message)
  } catch (e) {
    ElMessage.error(e.message || '切换失败')
    // 切回原值
    currentModel.value = 'deepseek'
  }
}
</script>

<style scoped>
.main-layout {
  height: 100vh;
}

.sidebar {
  background: #1d1e2c;
  transition: width 0.3s;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.logo-text {
  white-space: nowrap;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  text-align: center;
}

.collapse-btn {
  color: #a0a3bd !important;
}

/* 对话历史区域 */
.history-section {
  flex-shrink: 0;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.history-divider {
  margin: 8px 16px;
  border-color: rgba(255, 255, 255, 0.08);
}

.history-divider-text {
  color: #6c6e7e;
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
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  margin-bottom: 2px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}

.history-item:hover {
  background: rgba(255, 255, 255, 0.06);
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
  color: #6c6e7e;
  font-size: 10px;
  font-family: monospace;
  margin-top: 2px;
}

.history-item-time {
  color: #6c6e7e;
  font-size: 10px;
  flex-shrink: 0;
  margin-left: 8px;
}

.history-empty {
  color: #6c6e7e;
  font-size: 12px;
  text-align: center;
  padding: 12px 0;
}

.main-content {
  background: #f5f7fa;
}

.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  padding: 0 20px;
  z-index: 1;
}

.header-left h3 {
  font-size: 16px;
  color: #303133;
}

.content {
  padding: 20px;
  overflow: auto;
}
</style>
