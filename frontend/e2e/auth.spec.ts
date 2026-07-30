import { test, expect } from '@playwright/test'

test.describe.serial('登录认证', () => {
  test('未登录访问 /chat 自动跳转到 /login', async ({ page }) => {
    await page.goto('/chat')
    await expect(page).toHaveURL(/\/login/)
  })

  test('登录页显示快捷登录按钮', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('.demo-tag')).toHaveCount(7)
  })

  test('输入正确账号密码登录成功', async ({ page }) => {
    await page.goto('/login')
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/chat/)
    await expect(page.locator('.sidebar')).toBeVisible()
  })

  test('输入错误密码显示错误提示', async ({ page }) => {
    await page.goto('/login')
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'wrongpass')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/login/)
  })

  test('快捷登录按钮自动填充并登录', async ({ page }) => {
    await page.goto('/login')
    await page.waitForSelector('.demo-tag')
    await page.locator('.demo-tag').filter({ hasText: '采购部' }).click()
    await page.waitForURL(/\/chat/, { timeout: 15000 })
  })

  test('登录后刷新保持登录状态', async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.reload()
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/chat/)
    await page.reload()
    await expect(page.locator('.sidebar')).toBeVisible()
  })

  test('退出登录回到登录页', async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.reload()
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/chat/)
    await page.click('.user-dropdown')
    await page.waitForSelector('.el-dropdown-menu__item')
    await page.click('.el-dropdown-menu__item:has-text("退出")')
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 })
  })
})
