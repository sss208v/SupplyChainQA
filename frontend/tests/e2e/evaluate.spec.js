import { test, expect } from '@playwright/test'

test.describe('RAG 评估页面', () => {
  test('在线评估区域', async ({ page }) => {
    await page.goto('/evaluate')
    await expect(page.locator(':has-text("在线检索评估")').first()).toBeVisible()
  })

  test('查询输入框', async ({ page }) => {
    await page.goto('/evaluate')
    const input = page.locator('input[placeholder*="测试查询"]')
    await expect(input).toBeVisible()
    await input.fill('什么是RAG')
    await expect(input).toHaveValue('什么是RAG')
  })

  test('离线评估区域', async ({ page }) => {
    await page.goto('/evaluate')
    await expect(page.locator(':has-text("离线评估")').first()).toBeVisible()
  })
})
