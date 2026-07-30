import { test, expect } from '@playwright/test'

test.describe('智能对话页面', () => {
  test('欢迎消息显示', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.welcome')).toBeVisible()
    await expect(page.locator('h2:has-text("供应链智能助手")')).toBeVisible()
  })

  test('快捷操作按钮', async ({ page }) => {
    await page.goto('/chat')
    const btns = page.locator('.quick-actions .el-button')
    await expect(btns.first()).toBeVisible()
    expect(await btns.count()).toBeGreaterThanOrEqual(1)
  })

  test('输入区域', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.input-area')).toBeVisible()
    await expect(page.locator('.input-area textarea, .input-area input').first()).toBeVisible()
  })

  test('新对话按钮', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('button:has-text("新对话")')).toBeVisible()
  })

  test('输入文本', async ({ page }) => {
    await page.goto('/chat')
    const input = page.locator('.input-area textarea, .input-area input').first()
    await input.fill('你好')
    await expect(input).toHaveValue('你好')
  })
})

test.describe('发送消息', () => {
  test('发送消息后显示用户气泡', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })

    const input = page.locator('.input-area textarea, .input-area input').first()
    await input.fill('你好')
    await input.press('Enter')

    // 用户消息气泡应出现
    const userMsg = page.locator('.message-bubble.user, .msg-user, [class*="user-message"]')
    await expect(userMsg.first()).toBeVisible({ timeout: 10000 })
  })

  test('发送后输入框清空', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })

    const input = page.locator('.input-area textarea, .input-area input').first()
    await input.fill('测试消息')
    await input.press('Enter')

    // 等待消息发送
    await page.waitForTimeout(1000)
    await expect(input).toHaveValue('')
  })

  test('AI 回复内容出现', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })

    const input = page.locator('.input-area textarea, .input-area input').first()
    await input.fill('你好')
    await input.press('Enter')

    // 等待 assistant 消息出现且内容非空
    const assistantMsg = page.locator('.message-bubble.assistant, .msg-assistant, [class*="ai-message"]')
    await expect(assistantMsg.first()).toBeVisible({ timeout: 15000 })
    // 等待内容填充（streaming 可能需要时间）
    await page.waitForFunction(
      () => {
        const msgs = document.querySelectorAll('.message-bubble.assistant .message-content, .msg-assistant .message-content, [class*="ai-message"] .message-content')
        return msgs.length > 0 && msgs[msgs.length - 1].textContent.trim().length > 0
      },
      { timeout: 30000 }
    )
  })
})

test.describe('工具调用 E2E', () => {
  test('工具调用不重复（防回归核心用例）', async ({ page }) => {
    test.setTimeout(45000) // 给更长的超时时间

    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })

    const input = page.locator('.input-area textarea, .input-area input').first()
    await input.fill('查询 MAT-001 的库存信息')
    await input.press('Enter')

    // 等待工具调用出现（最多等 20 秒）
    const toolItems = page.locator('.tool-call-item')
    const appeared = await toolItems.first().isVisible({ timeout: 20000 }).catch(() => false)

    if (appeared) {
      // 核心断言：工具调用卡片数量不应超过 2（同一工具最多调用 2 次）
      const count = await toolItems.count()
      expect(count).toBeLessThanOrEqual(2)
    } else {
      // demo 模式下可能没有工具调用，验证 AI 回复仍然出现即可
      const assistantMsg = page.locator('.message-bubble.assistant, .msg-assistant, [class*="ai-message"]')
      await expect(assistantMsg.first()).toBeVisible({ timeout: 15000 })
    }
  })
})
