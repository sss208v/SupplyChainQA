import { test, expect } from '@playwright/test'
import { clearAuth } from './helpers.js'

test.describe('安全性', () => {
  test('token 存储在 localStorage', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    const token = await page.evaluate(() => localStorage.getItem('token'))
    expect(token).toBeTruthy()
  })

  test('用户信息存储', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    const user = JSON.parse(await page.evaluate(() => localStorage.getItem('user')))
    expect(user.username).toBe('admin')
    expect(user.role).toBeTruthy()
  })

  test('XSS: 脚本标签不执行', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    let alerted = false
    page.on('dialog', () => { alerted = true })
    await page.locator('.input-area textarea, .input-area input').first().fill('<script>alert(1)</script>')
    await page.waitForTimeout(1000)
    expect(alerted).toBeFalsy()
  })

  test('密码输入框类型安全', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('input[placeholder="密码"]')).toHaveAttribute('type', 'password')
  })

  test('登出后清除存储', async ({ page }) => {
    await page.goto('/chat')
    await page.waitForSelector('.chat-container', { timeout: 10000 })
    await clearAuth(page)
    await page.goto('/dashboard')
    await page.waitForTimeout(1000)
    expect(page.url()).toContain('/login')
  })
})
