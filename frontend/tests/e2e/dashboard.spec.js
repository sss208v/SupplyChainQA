import { test, expect } from '@playwright/test'

test.describe('系统概览页面', () => {
  test('页面标题', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('h2:has-text("系统概览")')).toBeVisible({ timeout: 10000 })
  })

  test('服务状态卡片', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('.status-card').first()).toBeVisible({ timeout: 10000 })
  })

  test('统计卡片', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('.stat-card, .el-statistic').first()).toBeVisible({ timeout: 10000 })
  })

  test('技术栈区域', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('.tech-card, :has-text("技术栈")').first()).toBeVisible({ timeout: 10000 })
  })
})
