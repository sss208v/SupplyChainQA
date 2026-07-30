import { test, expect } from '@playwright/test'

test.describe('工具管理页面', () => {
  test('导航到工具页面', async ({ page }) => {
    await page.goto('/tools')
    await page.waitForTimeout(2000)
    expect(page.url()).toContain('/tools')
  })

  test('页面内容加载', async ({ page }) => {
    await page.goto('/tools')
    await expect(page.locator('.tools-page')).toBeVisible({ timeout: 15000 })
  })
})
