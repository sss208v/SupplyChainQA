import { test, expect } from '@playwright/test'

test.describe('跨页面用户旅程', () => {
  test('完整导航流程', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.chat-container')).toBeVisible({ timeout: 10000 })

    await page.click('.el-menu-item:has-text("系统概览")')
    await page.waitForURL('**/dashboard', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("知识库管理")')
    await page.waitForURL('**/knowledge', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("RAG 评估")')
    await page.waitForURL('**/evaluate', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("智能对话")')
    await page.waitForURL('**/chat', { timeout: 10000 })
  })

  test('登录状态刷新保持', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.chat-container')).toBeVisible({ timeout: 10000 })
    await page.reload()
    await page.waitForTimeout(1000)
    expect(page.url()).toContain('/chat')
  })

  test('根路径重定向', async ({ page }) => {
    await page.goto('/')
    await page.waitForTimeout(2000)
    expect(page.url()).toContain('/dashboard')
  })
})
