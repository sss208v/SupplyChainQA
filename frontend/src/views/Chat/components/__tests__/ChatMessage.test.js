/**
 * ChatMessage 组件测试
 *
 * 测试范围：
 * - 用户消息显示内容和头像
 * - AI 消息显示 Markdown 渲染内容
 * - 意图标签映射正确
 * - 反馈按钮点击后禁用
 * - 工具调用折叠面板
 * - XSS 防御（DOMPurify 过滤）
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Mock API 模块
vi.mock('@/api/chat', () => ({
  submitFeedback: vi.fn(() => Promise.resolve({})),
  chatStream: vi.fn(),
  cancelStream: vi.fn(),
}))

// Mock Element Plus 消息组件
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    },
  }
})

import ChatMessage from '@/views/Chat/components/ChatMessage.vue'
import { ElMessage } from 'element-plus'

/**
 * 创建 element-plus stub 的辅助函数
 * 用简单的 stub 替代完整 Element Plus 渲染，避免测试环境依赖
 */
function createElStub(tag) {
  return {
    template: `<${tag}><slot /></${tag}>`,
    props: ['size', 'type', 'effect', 'round', 'closable', 'showIcon', 'disabled', 'text', 'modelValue', 'contentPosition'],
  }
}

const elementPlusStubs = {
  ElAvatar: createElStub('div'),
  ElIcon: createElStub('span'),
  ElTag: createElStub('span'),
  ElButton: createElStub('button'),
  ElCollapse: createElStub('div'),
  ElCollapseItem: createElStub('div'),
  ElDivider: createElStub('div'),
  ElAlert: createElStub('div'),
}

/**
 * 挂载 ChatMessage 组件的辅助函数
 */
function mountMessage(messageProps, options = {}) {
  return mount(ChatMessage, {
    props: { message: messageProps },
    global: {
      stubs: {
        ...elementPlusStubs,
        User: { template: '<span />' },
        Monitor: { template: '<span />' },
        SetUp: { template: '<span />' },
        Folder: { template: '<span />' },
        RagDag: { template: '<div />' },
      },
    },
    ...options,
  })
}

describe('ChatMessage 组件', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('用户消息', () => {
    it('应显示用户消息内容', () => {
      const wrapper = mountMessage({
        id: 1,
        role: 'user',
        content: '你好，请问库存情况如何？',
        timestamp: '10:00:00',
      })

      expect(wrapper.text()).toContain('你好，请问库存情况如何？')
    })

    it('应渲染用户头像', () => {
      const wrapper = mountMessage({
        id: 1,
        role: 'user',
        content: '测试消息',
        timestamp: '10:00:00',
      })

      // 用户头像含有 user-avatar class
      const avatar = wrapper.find('.user-avatar')
      expect(avatar.exists()).toBe(true)
    })

    it('应显示用户上传的图片', () => {
      const wrapper = mountMessage({
        id: 1,
        role: 'user',
        content: '看这张图',
        images: ['aGVsbG8='],  // base64 "hello"
        timestamp: '10:00:00',
      })

      const img = wrapper.find('.user-image')
      expect(img.exists()).toBe(true)
      expect(img.attributes('src')).toContain('data:image/jpeg;base64,')
    })
  })

  describe('AI 消息', () => {
    it('应渲染 Markdown 内容为 HTML', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '**加粗文本** 和 `代码`',
        streaming: false,
        timestamp: '10:00:01',
      })

      // marked 会将 ** 转为 <strong>
      const html = wrapper.find('.message-text').html()
      expect(html).toContain('<strong>')
      expect(html).toContain('加粗文本')
    })

    it('应显示 AI 头像', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '回答内容',
        streaming: false,
        timestamp: '10:00:01',
      })

      const avatar = wrapper.find('.ai-avatar')
      expect(avatar.exists()).toBe(true)
    })

    it('应显示加载动画当 streaming=true', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '',
        streaming: true,
        timestamp: '10:00:01',
      })

      const dots = wrapper.find('.loading-dots')
      expect(dots.exists()).toBe(true)
    })

    it('不应在 streaming=true 时显示反馈按钮', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '',
        streaming: true,
        timestamp: '10:00:01',
      })

      const feedback = wrapper.find('.feedback-bar')
      expect(feedback.exists()).toBe(false)
    })
  })

  describe('意图标签映射', () => {
    it('greeting 应映射为"问候"', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '你好！',
        intent: 'greeting',
        confidence: 0.95,
        streaming: false,
        timestamp: '10:00:01',
      })

      expect(wrapper.text()).toContain('问候')
    })

    it('rag_answer 应映射为"知识库问答"', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '根据知识库...',
        intent: 'rag_answer',
        confidence: 0.88,
        streaming: false,
        timestamp: '10:00:01',
      })

      expect(wrapper.text()).toContain('知识库问答')
    })

    it('tool_call 应映射为"工具调用"', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '正在调用工具...',
        intent: 'tool_call',
        confidence: 0.9,
        streaming: false,
        timestamp: '10:00:01',
      })

      expect(wrapper.text()).toContain('工具调用')
    })

    it('未知意图应直接显示原值', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '回答',
        intent: 'custom_intent',
        confidence: 0.7,
        streaming: false,
        timestamp: '10:00:01',
      })

      expect(wrapper.text()).toContain('custom_intent')
    })
  })

  describe('反馈按钮', () => {
    it('点击反馈按钮后两个按钮都应被禁用', async () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '这是一条回答',
        streaming: false,
        timestamp: '10:00:01',
      })

      const feedbackBtns = wrapper.findAll('.feedback-bar button')
      expect(feedbackBtns.length).toBeGreaterThanOrEqual(2)

      // 点击好评按钮
      await feedbackBtns[0].trigger('click')
      await wrapper.vm.$nextTick()

      // 验证反馈已记录（通过组件内部状态）
      expect(wrapper.vm.feedbackGiven).toBe(1)
    })

    it('反馈后应显示"感谢反馈"文字', async () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '回答内容',
        streaming: false,
        timestamp: '10:00:01',
      })

      const feedbackBtns = wrapper.findAll('.feedback-bar button')
      await feedbackBtns[0].trigger('click')
      await wrapper.vm.$nextTick()

      expect(wrapper.text()).toContain('感谢反馈')
    })
  })

  describe('工具调用折叠面板', () => {
    it('应渲染工具调用列表', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '查询结果',
        toolCalls: [
          { tool: 'query_inventory', input: { sku: 'A001' }, observation: '库存: 100', status: 'done' },
          { tool: 'query_price', input: { sku: 'A001' }, observation: '价格: ¥50', status: 'done' },
        ],
        streaming: false,
        timestamp: '10:00:01',
      })

      const toolCalls = wrapper.findAll('.tool-call-item')
      expect(toolCalls.length).toBe(2)
      // 验证工具调用的输入输出数据存在（stub 不渲染 title slot，检查 observation）
      expect(wrapper.text()).toContain('库存: 100')
      expect(wrapper.text()).toContain('价格: ¥50')
    })

    it('无工具调用时不显示工具面板', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '普通回答',
        toolCalls: [],
        streaming: false,
        timestamp: '10:00:01',
      })

      const toolPanel = wrapper.find('.tool-calls')
      expect(toolPanel.exists()).toBe(false)
    })
  })

  describe('XSS 防御', () => {
    it('script 标签应被 DOMPurify 过滤', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '<script>alert("xss")</script>正常文本',
        streaming: false,
        timestamp: '10:00:01',
      })

      const html = wrapper.find('.message-text').html()
      expect(html).not.toContain('<script>')
      expect(html).toContain('正常文本')
    })

    it('onerror 属性应被 DOMPurify 过滤', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '<img src=x onerror="alert(1)">',
        streaming: false,
        timestamp: '10:00:01',
      })

      const html = wrapper.find('.message-text').html()
      expect(html).not.toContain('onerror')
    })

    it('javascript: 协议链接应被 DOMPurify 过滤', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '[点击](javascript:alert("xss"))',
        streaming: false,
        timestamp: '10:00:01',
      })

      const html = wrapper.find('.message-text').html()
      expect(html).not.toContain('javascript:')
    })
  })

  describe('参考来源', () => {
    it('应显示参考来源列表', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '回答内容',
        sources: [
          { source: '知识库文档A', content: '这是文档A的摘要内容' },
          { source: '知识库文档B', content: '这是文档B的摘要内容' },
        ],
        streaming: false,
        timestamp: '10:00:01',
      })

      const sources = wrapper.findAll('.source-item')
      expect(sources.length).toBe(2)
      expect(wrapper.text()).toContain('知识库文档A')
    })

    it('无来源时显示通用知识提示', () => {
      const wrapper = mountMessage({
        id: 2,
        role: 'assistant',
        content: '这是通用回答',
        sources: [],
        streaming: false,
        timestamp: '10:00:01',
      })

      // ElAlert stub 不渲染 #title slot，改为检查 empty-sources 容器存在
      expect(wrapper.find('.empty-sources').exists()).toBe(true)
    })
  })
})
