import { test, expect } from '@playwright/test'

test.describe('导航和布局', () => {
  test('侧边栏显示', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    await expect(page.locator('.sidebar, .el-aside')).toBeVisible()
  })

  test('Logo 显示', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    await expect(page.locator('.logo')).toContainText('供应链助手')
  })

  test('5个导航菜单项', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    expect(await page.locator('.el-menu-item').count()).toBeGreaterThanOrEqual(5)
  })

  test('导航到各页面', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("系统概览")')
    await page.waitForURL('**/dashboard', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("知识库管理")')
    await page.waitForURL('**/knowledge', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("工具管理")')
    await page.waitForURL('**/tools', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("RAG 评估")')
    await page.waitForURL('**/evaluate', { timeout: 10000 })

    await page.click('.el-menu-item:has-text("智能对话")')
    await page.waitForURL('**/chat', { timeout: 10000 })
  })

  test('页面标题动态更新', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForTimeout(1000)
    expect(await page.title()).toContain('智能对话')
  })
})
