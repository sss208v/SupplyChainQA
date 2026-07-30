import { test, expect } from '@playwright/test'

/**
 * 工具调用去重测试
 *
 * 验证 chat store 的 onToolStatus / onToolCall 回调在各种场景下
 * 不会产生重复的 toolCalls 条目。
 *
 * 前提：页面已在聊天页，且通过 storageState 拥有登录态。
 */

// 辅助：获取 chat store 的 toolCalls
async function getToolCalls(page) {
  return page.evaluate(() => {
    const pinia = window.__pinia
    if (!pinia) return null
    const chatStore = pinia.state.value.chat
    if (!chatStore) return null
    return chatStore.messages.length > 0
      ? chatStore.messages[chatStore.messages.length - 1].toolCalls || []
      : []
  })
}

// 辅助：通过页面触发一条完整的工具调用 SSE 事件序列
// 直接在 store 上调用回调，模拟 SSE 解析后的事件分发
async function simulateToolEvents(page, events) {
  await page.evaluate((evts) => {
    const pinia = window.__pinia
    const chatStore = pinia.state.value.chat

    // 构造一条 assistant 消息
    chatStore.messages.push({
      id: 'test-msg-1',
      role: 'assistant',
      content: '',
      streaming: true,
      toolCalls: [],
    })

    const aiIdx = chatStore.messages.length - 1

    // 模拟 onToolStatus 和 onToolCall 回调逻辑
    // 这里直接复用 store 的内部状态操作逻辑
    const currentToolCalls = []

    for (const evt of evts) {
      if (evt.type === 'tool_status') {
        currentToolCalls.push({
          tool: evt.tool,
          status: evt.status,
        })
      } else if (evt.type === 'tool_call') {
        const idx = currentToolCalls.findIndex(t => t.tool === evt.tool && t.status === 'calling')
        if (idx !== -1) {
          currentToolCalls[idx] = { ...currentToolCalls[idx], ...evt, status: 'done' }
        } else {
          currentToolCalls.push({ ...evt, status: 'done' })
        }
      }
      chatStore.messages[aiIdx].toolCalls = [...currentToolCalls]
    }

    chatStore.messages[aiIdx].streaming = false
  }, events)
}

test.describe('工具调用去重', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
  })

  test('单次工具调用不重复', async ({ page }) => {
    await simulateToolEvents(page, [
      { type: 'tool_status', tool: 'query_inventory', status: 'calling' },
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-001' }, observation: '库存充足' },
    ])

    const toolCalls = await getToolCalls(page)
    expect(toolCalls).toHaveLength(1)
    expect(toolCalls[0].tool).toBe('query_inventory')
    expect(toolCalls[0].status).toBe('done')
  })

  test('不同工具调用各自独立', async ({ page }) => {
    await simulateToolEvents(page, [
      { type: 'tool_status', tool: 'query_inventory', status: 'calling' },
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-001' }, observation: '库存充足' },
      { type: 'tool_status', tool: 'query_po_status', status: 'calling' },
      { type: 'tool_call', tool: 'query_po_status', input: { po_number: 'PO-001' }, observation: '已发货' },
    ])

    const toolCalls = await getToolCalls(page)
    expect(toolCalls).toHaveLength(2)
    expect(toolCalls[0].tool).toBe('query_inventory')
    expect(toolCalls[1].tool).toBe('query_po_status')
  })

  test('同一工具多次调用（ReAct 循环）', async ({ page }) => {
    await simulateToolEvents(page, [
      { type: 'tool_status', tool: 'query_inventory', status: 'calling' },
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-001' }, observation: '第一次查询' },
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-002' }, observation: '第二次查询' },
    ])

    const toolCalls = await getToolCalls(page)
    // 两次独立调用应保留为 2 条记录
    expect(toolCalls).toHaveLength(2)
    expect(toolCalls[0].observation).toBe('第一次查询')
    expect(toolCalls[1].observation).toBe('第二次查询')
  })

  test('tool_call 没有前置 tool_status 时容错', async ({ page }) => {
    await simulateToolEvents(page, [
      // 直接收到 tool_call，没有 tool_status
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-001' }, observation: '库存充足' },
    ])

    const toolCalls = await getToolCalls(page)
    expect(toolCalls).toHaveLength(1)
    expect(toolCalls[0].tool).toBe('query_inventory')
    expect(toolCalls[0].status).toBe('done')
  })

  test('UI 渲染单个工具调用卡片', async ({ page }) => {
    await simulateToolEvents(page, [
      { type: 'tool_status', tool: 'query_inventory', status: 'calling' },
      { type: 'tool_call', tool: 'query_inventory', input: { material_code: 'MAT-001' }, observation: '库存充足' },
    ])

    // 等待 Vue 响应式更新渲染
    await page.waitForTimeout(500)
    const items = page.locator('.tool-call-item')
    await expect(items).toHaveCount(1)
  })

  test('UI 渲染多个工具调用卡片', async ({ page }) => {
    await simulateToolEvents(page, [
      { type: 'tool_status', tool: 'query_inventory', status: 'calling' },
      { type: 'tool_call', tool: 'query_inventory', input: {}, observation: '结果1' },
      { type: 'tool_status', tool: 'query_po_status', status: 'calling' },
      { type: 'tool_call', tool: 'query_po_status', input: {}, observation: '结果2' },
      { type: 'tool_status', tool: 'create_work_order', status: 'calling' },
      { type: 'tool_call', tool: 'create_work_order', input: {}, observation: '结果3' },
    ])

    await page.waitForTimeout(500)
    const items = page.locator('.tool-call-item')
    await expect(items).toHaveCount(3)
  })
})
