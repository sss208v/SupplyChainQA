import { test, expect } from '@playwright/test'
import { clearAuth } from './helpers.js'

test.describe('登录页面', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuth(page)
  })

  test('页面基本元素加载', async ({ page }) => {
    await expect(page.locator('.login-card')).toBeVisible()
    await expect(page.locator('h1')).toContainText('供应链智能助手')
    await expect(page.locator('input[placeholder="用户名"]')).toBeVisible()
    await expect(page.locator('input[placeholder="密码"]')).toBeVisible()
    await expect(page.locator('button:has-text("登录")')).toBeVisible()
  })

  test('7个演示账号标签', async ({ page }) => {
    await expect(page.locator('.demo-accounts')).toBeVisible()
    await expect(page.locator('.demo-tag')).toHaveCount(7)
  })

  test('空表单提交触发验证', async ({ page }) => {
    await page.click('button:has-text("登录")')
    await expect(page.locator('.el-form-item__error').first()).toBeVisible({ timeout: 5000 })
  })

  test('密码输入框类型为 password', async ({ page }) => {
    await expect(page.locator('input[placeholder="密码"]')).toHaveAttribute('type', 'password')
  })

  test('输入框绑定正确', async ({ page }) => {
    await page.fill('input[placeholder="用户名"]', 'testuser')
    await page.fill('input[placeholder="密码"]', 'testpass')
    await expect(page.locator('input[placeholder="用户名"]')).toHaveValue('testuser')
    await expect(page.locator('input[placeholder="密码"]')).toHaveValue('testpass')
  })
})

test.describe('路由守卫', () => {
  test('未登录访问 /chat 重定向', async ({ page }) => {
    await clearAuth(page)
    await page.goto('/chat')
    await page.waitForURL('**/login', { timeout: 10000 })
  })

  test('未登录访问 /dashboard 重定向', async ({ page }) => {
    await clearAuth(page)
    await page.goto('/dashboard')
    await page.waitForURL('**/login', { timeout: 10000 })
  })

  test('已登录可访问 /chat', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.chat-container')).toBeVisible({ timeout: 10000 })
  })

  test('已登录可访问 /dashboard', async ({ page }) => {
    await page.goto('/dashboard')
    await page.waitForTimeout(1000)
    expect(page.url()).toContain('/dashboard')
  })

  test('登录状态刷新后保持', async ({ page }) => {
    await page.goto('/chat')
    await expect(page.locator('.chat-container')).toBeVisible({ timeout: 10000 })
    await page.reload()
    await page.waitForTimeout(1000)
    expect(page.url()).toContain('/chat')
  })
})
