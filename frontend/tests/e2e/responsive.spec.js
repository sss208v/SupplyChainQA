import { test, expect } from '@playwright/test'

test.describe('响应式设计', () => {
  test('桌面视口 - 登录页', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    await expect(page.locator('.login-card')).toBeVisible()
  })

  test('手机视口 - 登录页', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto('/login')
    await expect(page.locator('.login-card')).toBeVisible()
    const box = await page.locator('.login-card').boundingBox()
    expect(box.width).toBeLessThanOrEqual(375)
  })

  test('桌面 - 完整布局', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    await expect(page.locator('.sidebar, .el-aside')).toBeVisible()
  })
})

test.describe('页面性能', () => {
  test('登录页加载 < 5s', async ({ page }) => {
    const start = Date.now()
    await page.goto('/login')
    await page.locator('.login-card').waitFor({ state: 'visible', timeout: 10000 })
    expect(Date.now() - start).toBeLessThan(5000)
  })

  test('无静态资源加载失败', async ({ page }) => {
    const failed = []
    page.on('requestfailed', req => failed.push(req.url()))
    await page.goto('/login')
    await page.waitForTimeout(2000)
    expect(failed.filter(u => u.includes('.js') || u.includes('.css')).length).toBe(0)
  })
})
