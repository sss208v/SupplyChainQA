<template>
  <div class="chat-container">
      <!-- 顶部操作栏 -->
      <div class="chat-header">
        <el-button
          text
          size="small"
          :icon="Plus"
          @click="handleNewConversation"
          :disabled="chatStore.streaming"
        >
          新对话
        </el-button>
      </div>
      <!-- 消息列表 -->
      <div class="message-list" ref="messageListRef">
        <!-- 欢迎消息 -->
        <div v-if="chatStore.messages.length === 0" class="welcome">
          <el-icon :size="48" color="#409eff"><ChatDotRound /></el-icon>
          <h2>供应链智能助手</h2>
          <p>我可以帮你查询制度规范、库存订单、创建工单</p>
          <div class="quick-actions">
            <el-button
              v-for="item in quickActions"
              :key="item.text"
              round
              @click="handleQuickAction(item.text)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              {{ item.text }}
            </el-button>
          </div>
        </div>

        <!-- 消息气泡 -->
        <ChatMessage
          v-for="msg in chatStore.messages"
          :key="msg.id"
          :message="msg"
        />
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <div class="input-wrapper">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="2"
            :placeholder="chatStore.streaming ? '正在回复中...' : '输入你的问题...'"
            :disabled="chatStore.streaming"
            resize="none"
            @keydown.enter.exact.prevent="handleSend"
          />
          <div class="input-actions">
            <el-button
              type="primary"
              :icon="Promotion"
              :loading="chatStore.streaming"
              :disabled="!inputText.trim() || chatStore.streaming"
              @click="handleSend"
            >
              发送
            </el-button>
            <el-button
              :icon="Delete"
              @click="handleClear"
              :disabled="chatStore.streaming"
            >
              清空
            </el-button>
          </div>
        </div>
      </div>
    </div>
</template>

<script setup>
/**
 * 供应链智能助手 - 对话主页面
 *
 * 1. SSE 流式对话的 UI 层：消息列表 + 输入框 + 快捷操作
 * 2. 自动滚动到底部：每次消息更新后，smooth scroll 到最新消息
 * 3. Enter 发送 / Shift+Enter 换行：通过 @keydown.enter.exact 捕获
 */
import { ref, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Promotion, Delete, Plus } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from './components/ChatMessage.vue'

const chatStore = useChatStore()
const route = useRoute()
const inputText = ref('')
const messageListRef = ref(null)

// 监听路由 query 中的 session 参数（从侧边栏历史记录点击进入）
watch(
  () => route.query.session,
  (newSession) => {
    if (newSession && newSession !== chatStore.sessionId) {
      chatStore.sessionId = newSession
    }
  },
  { immediate: true }
)

// 当流式回复结束时，保存对话历史
watch(
  () => chatStore.streaming,
  (isStreaming, wasStreaming) => {
    if (wasStreaming && !isStreaming && chatStore.sessionId) {
      const userMsgs = chatStore.messages.filter(m => m.role === 'user')
      const lastQuery = userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : ''
      if (window.__smartqa_addHistory) {
        window.__smartqa_addHistory(chatStore.sessionId, lastQuery)
      }
    }
  }
)

function handleNewConversation() {
  chatStore.clearChat()
}

// 快捷操作（供应链场景）
const quickActions = [
  { text: '新供应商准入需要什么资质？', icon: 'OfficeBuilding' },
  { text: '帮我查一下物料MAT-001的库存', icon: 'Box' },
  { text: '安全库存的计算公式是什么？', icon: 'DataAnalysis' },
]

function handleSend() {
  const query = inputText.value.trim()
  if (!query || chatStore.streaming) return
  inputText.value = ''
  chatStore.sendMessage(query)
}

function handleQuickAction(text) {
  inputText.value = text
  handleSend()
}

function handleClear() {
  chatStore.clearChat()
}

// 自动滚动到底部（使用 requestAnimationFrame 节流，避免频繁 DOM 操作）
let scrollRAF = null
function scrollToBottom() {
  if (scrollRAF) cancelAnimationFrame(scrollRAF)
  scrollRAF = requestAnimationFrame(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
    scrollRAF = null
  })
}

watch(
  () => chatStore.messages.length,
  () => nextTick(scrollToBottom)
)

// 流式内容更新也滚动
watch(
  () => chatStore.currentContent,
  () => nextTick(scrollToBottom)
)
</script>

<style scoped>
.chat-container {
  height: calc(100vh - 96px);
  display: flex;
  flex-direction: column;
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-surface);
  overflow: hidden;
}

/* 顶部操作栏 */
.chat-header {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
  flex-shrink: 0;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6);
}

/* 欢迎界面 */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--color-text-secondary);
}

.welcome h2 {
  margin-top: var(--space-4);
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.welcome p {
  margin-top: var(--space-2);
  font-size: 14px;
  color: var(--color-text-placeholder);
}

.quick-actions {
  margin-top: var(--space-6);
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  justify-content: center;
}

/* 输入区域 */
.input-area {
  border-top: 1px solid var(--color-border);
  padding: var(--space-3) var(--space-5) var(--space-4);
  background: var(--color-bg-subtle);
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
}

.input-wrapper {
  max-width: 860px;
  margin: 0 auto;
}

.input-actions {
  margin-top: var(--space-2);
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
