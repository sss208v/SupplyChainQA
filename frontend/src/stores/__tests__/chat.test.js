/**
 * Chat Store 核心测试
 *
 * 测试范围：
 * - sendMessage：用户消息添加 + AI 占位创建
 * - clearChat：状态重置
 * - approveAction / denyAction：审批流程
 * - 消息 ID 唯一性
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock chat API 模块
// 关键：chatStream 返回的 Promise 需要能被 resolve（通过调用 onDone 回调）
let _resolveStream
vi.mock('@/api/chat', () => ({
  chatStream: vi.fn((params, callbacks) => {
    return new Promise((resolve) => {
      _resolveStream = () => {
        callbacks?.onDone?.()
        resolve()
      }
    })
  }),
  cancelStream: vi.fn(),
}))

import { useChatStore } from '@/stores/chat'
import { chatStream, cancelStream } from '@/api/chat'

describe('Chat Store', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useChatStore()
    vi.clearAllMocks()
    _resolveStream = null
  })

  describe('sendMessage', () => {
    it('应将用户消息添加到 messages 数组', async () => {
      const promise = store.sendMessage('你好')
      const userMsg = store.messages.find((m) => m.role === 'user')
      expect(userMsg).toBeDefined()
      expect(userMsg.content).toBe('你好')
      // 手动触发流式完成
      _resolveStream?.()
      await promise
    })

    it('应为 AI 回复创建 streaming=true 的占位消息', async () => {
      const promise = store.sendMessage('测试问题')
      const aiMsg = store.messages.find((m) => m.role === 'assistant')
      expect(aiMsg).toBeDefined()
      expect(aiMsg.content).toBe('')
      expect(aiMsg.streaming).toBe(true)
      _resolveStream?.()
      await promise
    })

    it('不应在 query 为空时发送消息', async () => {
      await store.sendMessage('   ')
      expect(store.messages).toHaveLength(0)
    })

    it('不应在 streaming=true 时重复发送', async () => {
      const promise1 = store.sendMessage('第一条')
      expect(store.streaming).toBe(true)
      // 第二条应被忽略
      await store.sendMessage('第二条')
      const userMsgs = store.messages.filter((m) => m.role === 'user')
      expect(userMsgs).toHaveLength(1)
      _resolveStream?.()
      await promise1
    })

    it('approved=true 时应修改用户消息显示内容', async () => {
      const promise = store.sendMessage('删除记录', { approved: true })
      const userMsg = store.messages.find((m) => m.role === 'user')
      expect(userMsg.content).toBe('确认执行：删除记录')
      _resolveStream?.()
      await promise
    })

    it('SSE onContent 回调应拼接流式内容', async () => {
      const promise = store.sendMessage('测试流式')
      // 模拟 SSE 推送内容
      chatStream.mock.calls[0]?.[1]?.onContent?.('你好')
      chatStream.mock.calls[0]?.[1]?.onContent?.('世界')
      const aiMsg = store.messages.find((m) => m.role === 'assistant')
      expect(aiMsg.content).toBe('你好世界')
      _resolveStream?.()
      await promise
    })

    it('SSE onSession 回调应设置 sessionId', async () => {
      const promise = store.sendMessage('测试')
      chatStream.mock.calls[0]?.[1]?.onSession?.('session-123')
      expect(store.sessionId).toBe('session-123')
      _resolveStream?.()
      await promise
    })
  })

  describe('clearChat', () => {
    it('应清空所有消息和状态', async () => {
      const promise = store.sendMessage('测试')
      chatStream.mock.calls[0]?.[1]?.onContent?.('部分内容')
      _resolveStream?.()
      await promise

      store.clearChat()

      expect(store.messages).toHaveLength(0)
      expect(store.sessionId).toBe('')
      expect(store.streaming).toBe(false)
      expect(store.connectionStatus).toBe('disconnected')
      expect(store.connectionError).toBe('')
    })

    it('应调用 cancelStream 取消进行中的请求', () => {
      store.clearChat()
      expect(cancelStream).toHaveBeenCalled()
    })
  })

  describe('approveAction', () => {
    it('应使用 approved 参数调用 sendMessage', async () => {
      const promise = store.sendMessage('查询')
      chatStream.mock.calls[0]?.[1]?.onApprovalRequest?.({
        tool: 'create_ticket',
        query: '创建工单',
      })
      _resolveStream?.()
      await promise

      expect(store.pendingApproval).toEqual({
        tool: 'create_ticket',
        query: '创建工单',
      })

      // approveAction 内部会调用 sendMessage
      const promise2 = store.approveAction()
      // 获取第二次 chatStream 调用的回调并触发完成
      const lastCallIdx = chatStream.mock.calls.length - 1
      _resolveStream?.()
      await promise2

      expect(store.pendingApproval).toBeNull()
      expect(chatStream).toHaveBeenCalledWith(
        expect.objectContaining({ approved: true, approved_tool: 'create_ticket' }),
        expect.any(Object),
      )
    })

    it('pendingApproval 为 null 时应直接返回', async () => {
      store.pendingApproval = null
      await store.approveAction()
      expect(chatStream).not.toHaveBeenCalled()
    })
  })

  describe('denyAction', () => {
    it('应添加取消消息到 messages', () => {
      store.pendingApproval = { tool: 'delete_data', query: '删除数据' }
      store.denyAction()

      const lastMsg = store.messages[store.messages.length - 1]
      expect(lastMsg.role).toBe('assistant')
      expect(lastMsg.content).toBe('已取消 delete_data 操作。')
      expect(store.pendingApproval).toBeNull()
    })

    it('pendingApproval 为 null 时应直接返回', () => {
      store.pendingApproval = null
      store.denyAction()
      expect(store.messages).toHaveLength(0)
    })
  })

  describe('消息 ID 唯一性', () => {
    it('快速连续发送的消息 ID 应不重复', async () => {
      const promise1 = store.sendMessage('问题1')
      _resolveStream?.()
      await promise1

      // 微小延迟确保 Date.now() 产生不同值
      await new Promise((r) => setTimeout(r, 5))

      const promise2 = store.sendMessage('问题2')
      _resolveStream?.()
      await promise2

      const ids = store.messages.map((m) => m.id)
      const uniqueIds = new Set(ids)
      expect(uniqueIds.size).toBe(ids.length)
    })
  })
})
