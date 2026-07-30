import { test, expect } from '@playwright/test'

test.describe('知识库管理页面', () => {
  test('统计卡片', async ({ page }) => {
    await page.goto('/knowledge')
    await expect(page.locator('.stat-card').first()).toBeVisible({ timeout: 10000 })
  })

  test('上传区域', async ({ page }) => {
    await page.goto('/knowledge')
    await expect(page.locator('.upload-card, :has-text("上传文档")').first()).toBeVisible({ timeout: 10000 })
  })

  test('部门权限选择', async ({ page }) => {
    await page.goto('/knowledge')
    await expect(page.locator('.permission-section, :has-text("可见部门")').first()).toBeVisible({ timeout: 10000 })
  })
})
