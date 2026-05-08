/**
 * SmartQA Pro - 对话状态管理
 *
 * 1. Pinia 是 Vue3 官方推荐的状态管理库，替代 Vuex
 * 2. defineStore 定义 store，useChatStore 在组件中使用
 * 3. state 是响应式数据，actions 是修改状态的方法
 * 4. SSE 流式对话的状态管理是核心难点：
 *    - messages 数组存储所有消息
 *    - streaming 标记是否正在流式接收
 *    - currentContent 拼接流式内容片段
 */
import { defineStore } from 'pinia'
import { chatStream, chatCompletions, cancelStream } from '@/api/chat'
import { ref, computed } from 'vue'

export const useChatStore = defineStore('chat', () => {
  // ---- 状态 ----
  const messages = ref([])          // 消息列表
  const sessionId = ref('')         // 当前会话ID
  const streaming = ref(false)      // 是否正在流式输出
  const currentContent = ref('')    // 当前流式拼接的内容
  const currentSources = ref([])    // 当前回答的参考来源
  const currentToolCalls = ref([])  // 当前回答的工具调用
  const currentIntent = ref('')     // 当前意图
  const currentConfidence = ref(0)  // 当前置信度
  const currentTokenUsage = ref(null) // 当前token用量
  const currentConfidenceDecision = ref(null) // 当前置信度决策
  const currentWebSearch = ref(null) // 当前Web搜索状态
  const pendingApproval = ref(null) // 待审批操作
  const currentDagProgress = ref(null) // 当前RAG DAG进度

  // ---- 计算属性 ----
  const messageCount = computed(() => messages.value.length)

  // ---- Actions ----

  /**
   * 发送消息（SSE流式）
   *
   * 1. 用户发送消息 → 添加到消息列表
   * 2. 创建AI消息占位（显示加载动画）
   * 3. 调用SSE接口，逐步接收内容
   * 4. 每收到一个content事件，更新AI消息内容（打字机效果）
   * 5. 收到sources/tool_call事件，更新附加信息
   * 6. 收到[DONE]，标记流式结束
   */
  async function sendMessage(query) {
    if (!query.trim() || streaming.value) return

    // 1. 添加用户消息
    messages.value.push({
      id: Date.now(),
      role: 'user',
      content: query,
      timestamp: new Date().toLocaleTimeString(),
    })

    // 2. 创建AI消息占位
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      sources: [],
      toolCalls: [],
      intent: '',
      confidence: 0,
      tokenUsage: null,
      dagProgress: null,
      timestamp: new Date().toLocaleTimeString(),
      streaming: true,
    })
    // 通过数组下标获取响应式代理（直接push的对象引用不是proxy）
    const aiIdx = messages.value.length - 1

    // 3. 开始流式接收
    streaming.value = true
    currentContent.value = ''
    currentSources.value = []
    currentToolCalls.value = []
    currentTokenUsage.value = null
    currentDagProgress.value = null

    try {
      console.log('[ChatStore] 开始 chatStream，streaming=true')
      await chatStream(
        {
          query,
          session_id: sessionId.value || undefined,
          stream: true,
        },
        // SSE 事件回调
        {
          onSession: (id) => {
            console.log('[ChatStore] onSession:', id)
            sessionId.value = id
          },
          onRoute: (data) => {
            console.log('[ChatStore] onRoute:', data)
            currentIntent.value = data.intent
            currentConfidence.value = data.confidence
            messages.value[aiIdx].intent = data.intent
            messages.value[aiIdx].confidence = data.confidence
          },
          onTokenUsage: (usage) => {
            currentTokenUsage.value = usage
            messages.value[aiIdx].tokenUsage = usage
            console.log('[ChatStore] onTokenUsage:', usage)
          },
          onConfidenceDecision: (data) => {
            currentConfidenceDecision.value = data
            messages.value[aiIdx].confidenceDecision = data
            console.log('[ChatStore] onConfidenceDecision:', data)
          },
          onWebSearch: (data) => {
            currentWebSearch.value = data
            messages.value[aiIdx].webSearch = data
            console.log('[ChatStore] onWebSearch:', data)
          },
          onSelfRag: (data) => {
            // Self-RAG 过滤结果
            messages.value[aiIdx].selfRag = data
            console.log('[ChatStore] onSelfRag:', data)
          },
          onQueryAnalysis: (data) => {
            // Query 复杂度分析结果
            messages.value[aiIdx].queryAnalysis = data
            console.log('[ChatStore] onQueryAnalysis:', data)
          },
          onClarify: (data) => {
            // 澄清提问：系统主动问用户补充信息
            messages.value[aiIdx].content = data.question
            messages.value[aiIdx].clarify = data
            messages.value[aiIdx].streaming = false
            streaming.value = false
            console.log('[ChatStore] onClarify:', data)
          },
          onApprovalRequest: (data) => {
            // 写操作审批请求
            pendingApproval.value = data
            messages.value[aiIdx].approvalRequest = data
            messages.value[aiIdx].streaming = false
            streaming.value = false
            console.log('[ChatStore] onApprovalRequest:', data)
          },
          onDagProgress: (data) => {
            currentDagProgress.value = data
            messages.value[aiIdx].dagProgress = { ...data }
            console.log('[ChatStore] onDagProgress:', data)
          },
          onContent: (content) => {
            // 拼接内容 → 打字机效果
            currentContent.value += content
            messages.value[aiIdx].content = currentContent.value
          },
          onSources: (data) => {
            currentSources.value = data.sources || []
            messages.value[aiIdx].sources = data.sources || []
            messages.value[aiIdx].confidence = data.confidence || 0
          },
          onToolStatus: (data) => {
            // 工具调用中状态
            currentToolCalls.value.push({
              tool: data.tool,
              status: data.status,
            })
            // 同步到 messages 数组，触发 Vue 响应式更新
            messages.value[aiIdx].toolCalls = [...currentToolCalls.value]
          },
          onToolCall: (data) => {
            currentToolCalls.value.push(data)
            messages.value[aiIdx].toolCalls = [...currentToolCalls.value]
          },
          onError: (msg) => {
            console.error('[ChatStore] onError:', msg)
            messages.value[aiIdx].content = `出错了：${msg}`
            messages.value[aiIdx].streaming = false
          },
          onDone: () => {
            console.log(`[ChatStore] onDone — streaming关闭，currentContent长度=${currentContent.value.length}`)
            messages.value[aiIdx].streaming = false
            streaming.value = false
          },
        }
      )
      console.log('[ChatStore] chatStream 返回，streaming=', streaming.value)
    } catch (err) {
      console.error('[ChatStore] chatStream 抛出异常:', err)
      messages.value[aiIdx].content = `请求失败：${err.message}`
      messages.value[aiIdx].streaming = false
      streaming.value = false
    }
  }

  /**
   * 发送消息（非流式，备用）
   */
  async function sendMessageSync(query) {
    if (!query.trim()) return

    messages.value.push({
      id: Date.now(),
      role: 'user',
      content: query,
      timestamp: new Date().toLocaleTimeString(),
    })

    try {
      const res = await chatCompletions({
        query,
        session_id: sessionId.value || undefined,
        stream: false,
      })
      sessionId.value = res.session_id
      messages.value.push({
        id: Date.now() + 1,
        role: 'assistant',
        content: res.answer,
        sources: res.sources || [],
        toolCalls: res.tool_calls || [],
        intent: res.intent,
        confidence: res.confidence,
        tokenUsage: res.token_usage || null,
        timestamp: new Date().toLocaleTimeString(),
      })
    } catch (err) {
      messages.value.push({
        id: Date.now() + 1,
        role: 'assistant',
        content: `请求失败：${err.message}`,
        timestamp: new Date().toLocaleTimeString(),
      })
    }
  }

  /**
   * 清空对话
   */
  function clearChat() {
    // 取消正在进行的 SSE 流
    cancelStream()
    messages.value = []
    sessionId.value = ''
    currentContent.value = ''
    currentSources.value = []
    currentToolCalls.value = []
    currentTokenUsage.value = null
    currentDagProgress.value = null
    streaming.value = false
  }

  async function approveAction() {
    if (!pendingApproval.value) return
    const { query, tool } = pendingApproval.value
    pendingApproval.value = null
    // 重新发送请求，标记 approved=true
    await sendMessage(`确认执行：${query}`)
  }

  function denyAction() {
    if (!pendingApproval.value) return
    const tool = pendingApproval.value.tool
    pendingApproval.value = null
    messages.value.push({
      id: Date.now(),
      role: 'assistant',
      content: `已取消 ${tool} 操作。`,
      timestamp: new Date().toLocaleTimeString(),
    })
  }

  return {
    messages,
    sessionId,
    streaming,
    currentContent,
    currentTokenUsage,
    currentConfidenceDecision,
    currentWebSearch,
    pendingApproval,
    messageCount,
    sendMessage,
    sendMessageSync,
    approveAction,
    denyAction,
    clearChat,
  }
})
