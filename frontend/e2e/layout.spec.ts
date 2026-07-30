import { test, expect } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test.describe.serial('布局与路由', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/login')
    await page.waitForSelector('input[placeholder="用户名"]')
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登录")')
    await expect(page).toHaveURL(/\/chat/)
  })

  test('侧边栏菜单项点击跳转', async ({ page }) => {
    await page.click('.sidebar-menu .el-menu-item:has-text("知识库管理")')
    await expect(page).toHaveURL(/\/knowledge/)
    await page.click('.sidebar-menu .el-menu-item:has-text("工具管理")')
    await expect(page).toHaveURL(/\/tools/)
    await page.click('.sidebar-menu .el-menu-item:has-text("系统概览")')
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test('侧边栏折叠按钮', async ({ page }) => {
    const sidebar = page.locator('.sidebar')
    await expect(sidebar).toBeVisible()
    await page.click('.collapse-btn')
    await expect(sidebar).toHaveCSS('width', '64px')
    await page.click('.collapse-btn')
    await expect(sidebar).toHaveCSS('width', '220px')
  })

  test('顶部显示在线状态', async ({ page }) => {
    await expect(page.locator('.header-right .el-tag--success')).toContainText('在线')
  })

  test('顶部显示模型选择器', async ({ page }) => {
    await expect(page.locator('.header-right .el-select')).toBeVisible()
  })

  test('移动端视口侧边栏隐藏', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 })
    await expect(page.locator('.sidebar')).not.toBeVisible()
    await expect(page.locator('.mobile-menu-btn')).toBeVisible()
  })
})
