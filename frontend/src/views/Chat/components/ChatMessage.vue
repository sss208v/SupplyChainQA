<template>
  <div :class="['message-bubble', message.role]">
    <!-- 用户消息 -->
    <template v-if="message.role === 'user'">
      <div class="message-content user-content">
        <p>{{ message.content }}</p>
      </div>
      <el-avatar :size="36" class="avatar user-avatar">
        <el-icon><User /></el-icon>
      </el-avatar>
    </template>

    <!-- AI消息 -->
    <template v-else>
      <el-avatar :size="36" class="avatar ai-avatar">
        <el-icon><Monitor /></el-icon>
      </el-avatar>
      <div class="message-content ai-content">
        <!-- 复制按钮 -->
        <el-button
          v-if="message.content && !message.streaming"
          class="copy-btn"
          text
          size="small"
          @click="handleCopy"
        >
          📋
        </el-button>
        <!-- 意图标签 + 置信度决策 -->
        <div v-if="message.intent && message.content" class="intent-tag">
          <el-tag
            :type="intentTagType"
            size="small"
            effect="plain"
            round
          >
            {{ intentLabel }}
          </el-tag>
          <el-tag
            v-if="message.confidence > 0"
            :type="confidenceTagType"
            size="small"
            effect="plain"
            round
          >
            置信度: {{ (message.confidence * 100).toFixed(0) }}%
          </el-tag>
          <!-- 三层置信度决策标签 -->
          <el-tag
            v-if="message.confidenceDecision"
            :type="decisionTagType"
            size="small"
            effect="plain"
            round
          >
            {{ decisionLabel }}
          </el-tag>
          <!-- Web搜索触发提示 -->
          <el-tag
            v-if="message.webSearch"
            type="warning"
            size="small"
            effect="plain"
            round
          >
            🌐 Web搜索补充
          </el-tag>
          <!-- Query复杂度分析标签 -->
          <el-tag
            v-if="message.queryAnalysis"
            :type="queryAnalysisTagType"
            size="small"
            effect="plain"
            round
          >
            {{ queryAnalysisLabel }}
          </el-tag>
        </div>

        <!-- 工具调用展示（默认折叠，只显示一行摘要） -->
        <div v-if="message.toolCalls && message.toolCalls.length" class="tool-calls">
          <el-collapse v-model="expandedTools">
            <div
              v-for="(tc, idx) in message.toolCalls"
              :key="idx"
              class="tool-call-item"
            >
              <el-collapse-item :name="idx">
                <template #title>
                  <div class="tool-call-header">
                    <el-icon><SetUp /></el-icon>
                    <span>{{ tc.tool }}</span>
                    <el-tag type="success" size="small" round>✓</el-tag>
                  </div>
                </template>
                <div class="tool-call-detail">
                  <p><strong>输入:</strong> {{ JSON.stringify(tc.input) }}</p>
                  <p><strong>输出:</strong> {{ tc.observation }}</p>
                </div>
              </el-collapse-item>
            </div>
          </el-collapse>
        </div>

        <!-- 消息正文（支持Markdown） -->
        <div class="message-text" v-html="renderedContent"></div>

        <!-- 参考来源 -->
        <div v-if="message.sources && message.sources.length" class="sources">
          <el-divider content-position="left">
            <el-icon><Folder /></el-icon> 参考来源
          </el-divider>
          <div
            v-for="(src, idx) in message.sources"
            :key="idx"
            class="source-item"
          >
            <el-tag type="info" size="small">{{ src.source || `来源 ${idx + 1}` }}</el-tag>
            <span class="source-text">{{ src.content?.slice(0, 100) }}...</span>
          </div>
        </div>

        <!-- RAG 流程 DAG 可视化（仅 RAG 类型意图时显示） -->
        <RagDag v-if="message.dagProgress" :dag-data="message.dagProgress" />

        <!-- 写操作审批按钮 -->
        <div v-if="message.approvalRequest" class="approval-bar">
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom: 8px;"
          >
            <template #title>
              即将执行写操作：{{ message.approvalRequest.tool }}
            </template>
          </el-alert>
          <div class="approval-actions">
            <el-button type="primary" size="small" @click="handleApprove">
              ✅ 确认执行
            </el-button>
            <el-button size="small" @click="handleDeny">
              ❌ 取消
            </el-button>
          </div>
        </div>

        <!-- 澄清提问标记 -->
        <div v-if="message.clarify" class="clarify-tag">
          <el-tag type="info" size="small" effect="plain" round>
            💬 需要补充信息
          </el-tag>
        </div>

        <!-- 反馈按钮（仅AI消息显示） -->
        <div v-if="!message.streaming && !message.approvalRequest" class="feedback-bar">
          <el-button
            text
            size="small"
            :class="{ 'feedback-active': feedbackGiven === 1 }"
            :disabled="feedbackGiven !== null"
            @click="submitFeedback(1)"
          >
            👍
          </el-button>
          <el-button
            text
            size="small"
            :class="{ 'feedback-active': feedbackGiven === -1 }"
            :disabled="feedbackGiven !== null"
            @click="submitFeedback(-1)"
          >
            👎
          </el-button>
          <span v-if="feedbackGiven !== null" class="feedback-thanks">感谢反馈</span>
        </div>

        <!-- 加载动画 -->
        <div v-if="message.streaming" class="loading-dots">
          <span></span><span></span><span></span>
        </div>

        <!-- Token用量标签 -->
        <div v-if="message.tokenUsage && !message.streaming" class="token-usage">
          <span>{{ message.tokenUsage.total_tokens }} tokens</span>
          <span v-if="message.tokenUsage.cost_yuan > 0">· ¥{{ message.tokenUsage.cost_yuan.toFixed(4) }}</span>
          <span v-if="message.tokenUsage.model">· {{ message.tokenUsage.model }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * SmartQA Pro - 消息气泡组件
 *
 * 1. 用户/AI消息的差异化展示：用户靠右蓝色，AI靠左灰色
 * 2. Markdown渲染：使用 marked 库将 AI 回复渲染为 HTML
 * 3. 工具调用折叠面板：展示 ReAct 循环的 Thought→Action→Observation
 * 4. 参考来源卡片：展示 RAG 检索命中的文档片段
 * 5. 用户反馈：👍👎 按钮收集用户满意度，支持优雅降级
 */
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

import { ElMessage } from 'element-plus'
import { User, Monitor } from '@element-plus/icons-vue'
import { submitFeedback as apiSubmitFeedback } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import RagDag from './RagDag.vue'

const chatStore = useChatStore()

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
})

/**
 * 复制消息内容到剪贴板
 */
function handleCopy() {
  if (!props.message.content) return
  navigator.clipboard.writeText(props.message.content).then(() => {
    ElMessage.success('已复制')
  }).catch(() => {
    ElMessage.error('复制失败')
  })
}

/**
 * 反馈状态追踪
 * null = 未反馈, 1 = 好评, -1 = 差评
 */
const feedbackGiven = ref(null)

// 工具调用折叠状态（空数组 = 全部折叠，点击展开）
const expandedTools = ref([])

/**
 * 提交用户反馈
 *
 * - 即使API调用失败，也记录用户的选择（避免重复点击）
 * - 用 ElMessage.warning 提示但不阻断用户操作
 *
 * @param {number} rating - 评分：1=好评，-1=差评
 */
const submitFeedback = async (rating) => {
  if (feedbackGiven.value !== null) return

  // 立即设置反馈状态，防止重复点击
  feedbackGiven.value = rating

  try {
    await apiSubmitFeedback({
      session_id: props.message.sessionId || '',
      query: '',
      answer: props.message.content || '',
      rating,
    })
    ElMessage.success('感谢您的反馈！')
  } catch (error) {
    // 优雅降级：API失败时仍然记录反馈，提示用户
    console.warn('[Feedback] API调用失败，已本地记录:', error)
    ElMessage.warning('反馈已记录（网络异常）')
  }
}

// Markdown渲染（DOMPurify 防 XSS）
const renderedContent = computed(() => {
  if (!props.message.content) return ''
  try {
    const rawHtml = marked(props.message.content)
    return DOMPurify.sanitize(rawHtml, {
      ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'a', 'span'],
      ALLOWED_ATTR: ['href', 'class', 'target'],
    })
  } catch {
    return props.message.content
  }
})

// 意图标签
const intentLabel = computed(() => {
  const map = {
    greeting: '问候',
    rag_answer: '知识库问答',
    tool_call: '工具调用',
    hybrid: '混合',
    unclear: '意图不明',
  }
  return map[props.message.intent] || props.message.intent
})

const intentTagType = computed(() => {
  const map = {
    greeting: '',
    rag_answer: 'success',
    tool_call: 'warning',
    hybrid: 'danger',
    unclear: 'info',
  }
  return map[props.message.intent] || 'info'
})

function handleApprove() {
  chatStore.approveAction()
}

function handleDeny() {
  chatStore.denyAction()
}

const confidenceTagType = computed(() => {
  const c = props.message.confidence
  if (c >= 0.8) return 'success'
  if (c >= 0.5) return 'warning'
  return 'danger'
})

const decisionLabel = computed(() => {
  const d = props.message.confidenceDecision
  if (!d) return ''
  const labels = {
    direct: '✅ 直接回答',
    rewrite: '🔄 改写重试',
    web_search: '🌐 Web搜索',
  }
  return labels[d.strategy] || d.strategy
})

const decisionTagType = computed(() => {
  const d = props.message.confidenceDecision
  if (!d) return 'info'
  const types = { direct: 'success', rewrite: 'warning', web_search: 'danger' }
  return types[d.strategy] || 'info'
})

const queryAnalysisLabel = computed(() => {
  const a = props.message.queryAnalysis
  if (!a) return ''
  const strategyLabels = {
    light: '⚡ 轻量检索',
    standard: '🔍 标准检索',
    full: '🔬 深度检索',
  }
  const base = strategyLabels[a.strategy] || a.strategy
  return `${base} (${a.complexity.toFixed(2)})`
})

const queryAnalysisTagType = computed(() => {
  const a = props.message.queryAnalysis
  if (!a) return 'info'
  const types = { light: 'success', standard: '', full: 'warning' }
  return types[a.strategy] || 'info'
})
</script>

<style scoped>
.message-bubble {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.message-bubble.user {
  justify-content: flex-end;
}

.message-bubble.assistant {
  justify-content: flex-start;
}

.avatar {
  flex-shrink: 0;
}

.user-avatar {
  background: #409eff;
}

.ai-avatar {
  background: #67c23a;
}

.message-content {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 14px;
}

.user-content {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.ai-content {
  background: #f4f6f8;
  color: #303133;
  border-bottom-left-radius: 4px;
  position: relative;
}

/* 复制按钮 */
.copy-btn {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 14px;
  opacity: 0;
  transition: opacity 0.2s;
  z-index: 1;
  padding: 2px 4px !important;
  min-height: auto !important;
  line-height: 1;
}

.ai-content:hover .copy-btn {
  opacity: 0.7;
}

.copy-btn:hover {
  opacity: 1 !important;
}

/* 意图标签 */
.intent-tag {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

/* 工具调用 */
.tool-calls {
  margin-bottom: 4px;
}

/* 折叠面板精简样式 */
.tool-calls :deep(.el-collapse) {
  border: none;
}
.tool-calls :deep(.el-collapse-item__header) {
  height: 28px;
  line-height: 28px;
  font-size: 12px;
  background: transparent;
  border: none;
}
.tool-calls :deep(.el-collapse-item__wrap) {
  border: none;
}
.tool-calls :deep(.el-collapse-item__content) {
  padding-bottom: 4px;
}

.tool-call-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
}

.tool-call-detail {
  font-size: 12px;
  color: #606266;
}

.tool-call-detail pre {
  background: #fff;
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 4px 0;
}

/* Markdown 内容 */
.message-text :deep(p) {
  margin: 4px 0;
}

.message-text :deep(code) {
  background: #e8eaec;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}

.message-text :deep(pre) {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.message-text :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

/* 参考来源 */
.sources {
  margin-top: 8px;
  font-size: 12px;
}

.source-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 4px;
}

.source-text {
  color: #909399;
  line-height: 1.4;
}

/**
 * 反馈按钮栏
 *
 * - 使用 flex 布局实现按钮水平排列
 * - 已选中按钮用 primary 色高亮，未选中按钮灰化
 * - 禁用状态下 pointer-events: none 防止重复点击
 */
.feedback-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding-top: 4px;
}

.feedback-bar .el-button {
  font-size: 16px;
  padding: 4px 8px;
  transition: all 0.2s ease;
}

.feedback-bar .el-button:disabled {
  opacity: 0.4;
}

/* 选中的反馈按钮高亮 */
.feedback-bar .feedback-active {
  color: #409eff !important;
  font-weight: bold;
  opacity: 1 !important;
}

/* 感谢反馈提示文字 */
.feedback-thanks {
  font-size: 12px;
  color: #909399;
  margin-left: 4px;
}

/* 加载动画（三个跳动的点） */
.loading-dots {
  display: flex;
  gap: 4px;
  padding-top: 4px;
}

.loading-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: bounce 1.4s infinite ease-in-out both;
}

.loading-dots span:nth-child(1) { animation-delay: 0s; }
.loading-dots span:nth-child(2) { animation-delay: 0.16s; }
.loading-dots span:nth-child(3) { animation-delay: 0.32s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

/* 审批栏 */
.approval-bar {
  margin-bottom: 8px;
}

.approval-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

/* 澄清标记 */
.clarify-tag {
  margin-bottom: 8px;
}

/* Token用量标签 */
.token-usage {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  font-size: 11px;
  color: #b0b3b8;
  font-family: 'SF Mono', 'Consolas', monospace;
}
</style>
