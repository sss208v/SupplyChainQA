import { test, expect } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test.describe.serial('对话功能', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/login')
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/chat/)
  })

  test('进入对话页显示欢迎消息', async ({ page }) => {
    await expect(page.locator('.welcome h2')).toContainText('供应链智能助手')
  })

  test('显示快捷操作按钮', async ({ page }) => {
    await expect(page.locator('.quick-actions .el-button')).toHaveCount(3)
  })

  test('输入框可以输入文本', async ({ page }) => {
    const input = page.locator('.input-area textarea')
    await input.fill('测试问题')
    await expect(input).toHaveValue('测试问题')
  })

  test('发送消息后消息出现在列表中', async ({ page }) => {
    await page.locator('.input-area textarea').fill('你好')
    await page.click('button:has-text("发送")')
    await expect(page.locator('.message-bubble.user .message-content p')).toContainText('你好', { timeout: 5000 })
  })

  test('快捷操作按钮点击自动发送', async ({ page }) => {
    await page.click('.quick-actions .el-button:first-child')
    await expect(page.locator('.message-bubble.user .message-content p')).toContainText('供应商', { timeout: 5000 })
  })

  test('新对话按钮清空消息', async ({ page }) => {
    await page.locator('.input-area textarea').fill('测试')
    await page.click('button:has-text("发送")')
    await expect(page.locator('.message-bubble.user')).toBeVisible({ timeout: 5000 })
    await page.click('button:has-text("新对话")')
    await expect(page.locator('.welcome h2')).toContainText('供应链智能助手')
  })
})
