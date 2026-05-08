/**
 * SmartQA Pro - 对话 API 接口
 *
 *
 * 1. SSE vs WebSocket：
 *    - SSE：单向（服务端→客户端），基于HTTP，自动重连，适合AI对话
 *    - WebSocket：双向，独立协议，适合实时协作
 *    - AI对话场景：只需要服务端推送内容 → SSE够用且更简单
 *
 * 2. 前端使用 fetch + ReadableStream 读取 SSE：
 *    - 不用 EventSource（因为它不支持POST请求和自定义headers）
 *    - 用 fetch 发POST请求，读取 response.body 流
 *    - 逐行解析 "data: {json}\n\n" 格式
 *
 * 3. SSE消息格式（后端定义的）：
 *    - {"type":"session", "session_id":"xxx"}     → 会话ID
 *    - {"type":"route", "intent":"rag_answer"}     → 意图路由结果
 *    - {"type":"content", "content":"xxx"}         → 内容片段
 *    - {"type":"sources", "sources":[...]}         → 参考来源
 *    - {"type":"tool_status", "status":"calling"}  → 工具调用状态
 *    - {"type":"tool_call", "tool":"weather"}      → 工具调用结果
 *    - {"type":"error", "message":"xxx"}           → 错误
 *    - [DONE]                                      → 流式结束
 */
import request from './request'

const API_PREFIX = '/api/v1'

/**
 * 获取模型列表
 */
export function listModels() {
  return request.get(`${API_PREFIX}/chat/model/list`)
}

/**
 * 切换模型
 */
export function switchModel(provider) {
  return request.post(`${API_PREFIX}/chat/model/switch`, { provider })
}

/**
 * 非流式对话
 */
export function chatCompletions(data) {
  return request.post(`${API_PREFIX}/chat/completions`, data)
}

/**
 * 提交用户反馈
 *
 * @param {Object} data - 反馈数据
 * @param {string} data.session_id - 会话ID
 * @param {string} data.query - 用户查询
 * @param {string} data.answer - AI回复
 * @param {number} data.rating - 评分：1=好评，-1=差评
 * @param {string} [data.comment] - 可选评论
 * @param {number} [data.confidence] - 置信度
 * @param {string} [data.intent] - 意图类型
 */
export function submitFeedback(data) {
  return request.post(`${API_PREFIX}/feedback`, data)
}

/**
 * 获取反馈统计数据
 */
export function getFeedbackStats() {
  return request.get(`${API_PREFIX}/feedback/stats`)
}

/**
 * SSE流式对话
 *
 * @param {Object} data - 请求参数 { query, session_id, stream }
 * @param {Object} callbacks - 事件回调
 *   - onSession(sessionId)
 *   - onRoute(data)
 *   - onContent(content)
 *   - onSources(data)
 *   - onToolStatus(data)
 *   - onToolCall(data)
 *   - onTokenUsage(data)
 *   - onConfidenceDecision(data)
 *   - onWebSearch(data)
 *   - onClarify(data)
 *   - onSelfRag(data)
 *   - onApprovalRequest(data)
 *   - onError(message)
 *   - onDone()
 */
/**
 * 当前进行中的 AbortController（用于取消流式请求）
 */
let currentController = null

/**
 * 取消当前进行中的流式请求
 */
export function cancelStream() {
  if (currentController) {
    currentController.abort()
    currentController = null
  }
}

export async function chatStream(data, callbacks = {}) {
  const url = `${API_PREFIX}/chat/stream`
  const t0 = performance.now()
  console.log(`[ChatStream +0ms] 请求发送:`, { url, data })

  // 创建 AbortController 用于取消请求
  currentController = new AbortController()
  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: currentController.signal,
    })
    console.log(`[ChatStream +${(performance.now()-t0).toFixed(0)}ms] HTTP状态:`, response.status, response.statusText)
  } catch (err) {
    console.error(`[ChatStream +${(performance.now()-t0).toFixed(0)}ms] fetch失败:`, err)
    if (err.name === 'AbortError') {
      console.log(`[ChatStream] 请求被用户取消`)
    } else {
      callbacks.onError?.(`网络请求失败: ${err.message}。请确认后端已启动且端口为 8001。`)
    }
    callbacks.onDone?.()
    return
  } finally {
    currentController = null
  }

  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body.detail || ''
    } catch {}
    console.error(`[ChatStream +${(performance.now()-t0).toFixed(0)}ms] HTTP错误:`, response.status, detail)
    callbacks.onError?.(`HTTP ${response.status}: ${detail || response.statusText}`)
    callbacks.onDone?.()
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventCount = 0
  let lastContentTime = t0

  console.log(`[ChatStream +${(performance.now()-t0).toFixed(0)}ms] 开始读取SSE流...`)

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    // 解码二进制数据为文本
    buffer += decoder.decode(value, { stream: true })

    // 按行解析SSE消息
    const lines = buffer.split('\n')
    buffer = lines.pop() || '' // 最后一行可能不完整，保留到下次

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || !trimmed.startsWith('data:')) continue

      const dataStr = trimmed.slice(5).trim()

      // 结束标记
      if (dataStr === '[DONE]') {
        const totalMs = (performance.now() - t0).toFixed(0)
        console.log(`[ChatStream +${totalMs}ms] === 流结束 === 共${eventCount}个事件，前端总耗时=${totalMs}ms`)
        callbacks.onDone?.()
        return
      }

      try {
        const parsed = JSON.parse(dataStr)
        eventCount++
        const eventMs = (performance.now() - t0).toFixed(0)
        console.log(`[ChatStream +${eventMs}ms 事件#${eventCount}]`, parsed.type, parsed)

        switch (parsed.type) {
          case 'session':
            callbacks.onSession?.(parsed.session_id)
            break
          case 'route':
            callbacks.onRoute?.(parsed)
            break
          case 'dag_progress':
            callbacks.onDagProgress?.(parsed)
            break
          case 'content':
            lastContentTime = performance.now()
            callbacks.onContent?.(parsed.content)
            break
          case 'sources':
            callbacks.onSources?.(parsed)
            break
          case 'tool_status':
            callbacks.onToolStatus?.(parsed)
            break
          case 'tool_call':
            callbacks.onToolCall?.(parsed)
            break
          case 'token_usage':
            callbacks.onTokenUsage?.(parsed.usage)
            break
          case 'confidence_decision':
            callbacks.onConfidenceDecision?.(parsed)
            break
          case 'web_search':
            callbacks.onWebSearch?.(parsed)
            break
          case 'clarify':
            callbacks.onClarify?.(parsed)
            break
          case 'self_rag':
            callbacks.onSelfRag?.(parsed)
            break
          case 'query_analysis':
            callbacks.onQueryAnalysis?.(parsed)
            break
          case 'approval_request':
            callbacks.onApprovalRequest?.(parsed)
            break
          case 'error':
            console.error(`[ChatStream +${eventMs}ms] 后端error事件:`, parsed.message)
            callbacks.onError?.(parsed.message)
            break
        }
      } catch (e) {
        // 解析失败的行跳过
        console.warn(`[ChatStream] SSE解析失败:`, e, '原始数据:', dataStr)
      }
    }
  }

  // 流正常结束但没收到 [DONE]
  const totalMs = (performance.now() - t0).toFixed(0)
  console.warn(`[ChatStream +${totalMs}ms] 流结束但未收到[DONE]，共${eventCount}个事件`)
  callbacks.onDone?.()
}
