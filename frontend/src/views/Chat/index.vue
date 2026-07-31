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
    <!-- 连接状态指示器 -->
    <div
      v-if="chatStore.connectionStatus !== 'connected'"
      :class="['connection-bar', chatStore.connectionStatus]"
    >
      <span class="status-dot"></span>
      <span class="status-text">
        <template v-if="chatStore.connectionStatus === 'connecting'"
          >正在连接...</template
        >
        <template v-else-if="chatStore.connectionStatus === 'error'"
          >连接异常：{{
            chatStore.connectionError || "请检查后端服务"
          }}</template
        >
        <template v-else-if="chatStore.connectionStatus === 'disconnected'"
          >未连接</template
        >
      </span>
      <el-button
        v-if="chatStore.connectionStatus === 'error' && chatStore.lastQuery"
        size="small"
        type="warning"
        plain
        @click="chatStore.retryLastMessage()"
        :loading="chatStore.streaming"
      >
        重试
      </el-button>
    </div>
    <!-- 消息列表 -->
    <div class="message-list" ref="messageListRef">
      <!-- 欢迎消息 -->
      <div v-if="chatStore.messages.length === 0" class="welcome">
        <el-icon :size="48" color="#2563eb"><ChatDotRound /></el-icon>
        <h2>供应链智能助手</h2>
        <p>我可以帮你查询制度规范、库存订单、创建工单</p>
        <!-- 演示模式横幅 -->
        <el-alert
          v-if="chatStore.demoMode.active"
          title="演示模式"
          type="warning"
          :description="
            chatStore.demoMode.message || 'LLM 未连接，当前为离线降级链路'
          "
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
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
          :placeholder="
            chatStore.streaming ? '正在回复中...' : '输入你的问题...'
          "
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
import { ref, nextTick, watch } from "vue";
import { useRoute } from "vue-router";
import { Promotion, Delete, Plus } from "@element-plus/icons-vue";
import { useChatStore } from "@/stores/chat";
import ChatMessage from "./components/ChatMessage.vue";

const chatStore = useChatStore();
const route = useRoute();
const inputText = ref("");
const messageListRef = ref(null);

// 监听路由 query 中的 session 参数（从侧边栏历史记录点击进入）
watch(
  () => route.query.session,
  (newSession) => {
    if (newSession && newSession !== chatStore.sessionId) {
      chatStore.sessionId = newSession;
    }
  },
  { immediate: true }
);

// 当流式回复结束时，保存对话历史
watch(
  () => chatStore.streaming,
  (isStreaming, wasStreaming) => {
    if (wasStreaming && !isStreaming && chatStore.sessionId) {
      const userMsgs = chatStore.messages.filter((m) => m.role === "user");
      const lastQuery =
        userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : "";
      if (window.__scqa_addHistory) {
        window.__scqa_addHistory(chatStore.sessionId, lastQuery);
      }
    }
  }
);

function handleNewConversation() {
  chatStore.clearChat();
}

// 快捷操作（供应链场景）
const quickActions = [
  { text: "新供应商准入需要什么资质？", icon: "OfficeBuilding" },
  { text: "帮我查一下物料MAT-001的库存", icon: "Box" },
  { text: "安全库存的计算公式是什么？", icon: "DataAnalysis" },
];

async function handleSend() {
  const query = inputText.value.trim();
  if (!query || chatStore.streaming) return;
  inputText.value = "";
  try {
    await chatStore.sendMessage(query);
  } finally {
    // 发送后清除图片（无论成功失败都执行）
    chatStore.clearImages();
  }
}

function handleQuickAction(text) {
  inputText.value = text;
  handleSend();
}

function handleClear() {
  chatStore.clearChat();
}

// 自动滚动到底部（使用 requestAnimationFrame 节流，避免频繁 DOM 操作）
let scrollRAF = null;
function scrollToBottom() {
  if (scrollRAF) cancelAnimationFrame(scrollRAF);
  scrollRAF = requestAnimationFrame(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight;
    }
    scrollRAF = null;
  });
}

watch(
  () => chatStore.messages.length,
  () => nextTick(scrollToBottom)
);

// 流式内容更新也滚动
watch(
  () => chatStore.currentContent,
  () => nextTick(scrollToBottom)
);
</script>

<style scoped>
.chat-container {
  height: calc(100vh - 96px);
  display: flex;
  flex-direction: column;
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-surface);
  overflow: hidden;
}

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

/* Welcome screen */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--color-text-secondary);
  animation: fadeIn 0.5s ease both;
}

.welcome h2 {
  margin-top: var(--space-4);
  font-family: var(--font-heading);
  font-size: 24px;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
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

.quick-actions .el-button {
  border-radius: var(--radius-full);
  height: 40px;
  padding: 0 20px;
  transition: all var(--transition-base);
}

.quick-actions .el-button:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-raised);
}

/* Input area */
.input-area {
  border-top: 1px solid var(--color-border-light);
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

/* Connection status bar */
.connection-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  font-size: 13px;
  border-bottom: 1px solid var(--color-border-light);
  flex-shrink: 0;
}
.connection-bar.connecting {
  background: #fffbeb;
  color: #d97706;
}
.connection-bar.error {
  background: #fef2f2;
  color: #dc2626;
}
.connection-bar.disconnected {
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.connection-bar.connecting .status-dot {
  background: #d97706;
  animation: pulse 1s infinite;
}
.connection-bar.error .status-dot {
  background: #dc2626;
}
.connection-bar.disconnected .status-dot {
  background: var(--color-text-placeholder);
}
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
.status-text {
  flex: 1;
}
.connection-bar .el-button {
  flex-shrink: 0;
}

/* ===== Mobile Responsive ===== */
@media (max-width: 767px) {
  .chat-container {
    height: calc(100vh - 72px);
    border-radius: 0;
    box-shadow: none;
  }

  .chat-header {
    padding: 6px 10px;
  }

  .chat-header .el-button {
    font-size: 12px;
    padding: 6px 10px;
  }

  .message-list {
    padding: 10px 8px;
  }

  .welcome h2 {
    font-size: 18px;
  }

  .welcome p {
    font-size: 13px;
    padding: 0 20px;
    text-align: center;
  }

  .quick-actions {
    flex-direction: column;
    width: 100%;
    padding: 0 16px;
  }

  .quick-actions .el-button {
    width: 100%;
    justify-content: flex-start;
    font-size: 13px;
  }

  .input-area {
    padding: 8px 10px 10px;
    border-radius: 0;
  }

  .input-wrapper {
    max-width: 100%;
  }

  .input-actions {
    gap: 6px;
  }

  .input-actions .el-button {
    font-size: 12px;
    padding: 6px 12px;
  }
}

/* Tablet */
@media (min-width: 768px) and (max-width: 1023px) {
  .chat-container {
    height: calc(100vh - 80px);
  }

  .input-wrapper {
    max-width: 100%;
  }
}
</style>
