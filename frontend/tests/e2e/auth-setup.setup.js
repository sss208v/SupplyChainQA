// This setup test runs ONCE before all other tests.
// It logs in via API and saves browser storageState for all subsequent tests.
import { test as setup, expect } from '@playwright/test'

const authFile = 'tests/.auth/user.json'

setup('login and save auth state', async ({ page, request }) => {
  // Login via API (only call this ONCE in the entire test suite)
  const loginRes = await request.post('http://127.0.0.1:8001/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(loginRes.ok()).toBeTruthy()
  const { token, user } = await loginRes.json()

  // Set localStorage in browser and save storageState
  await page.goto('/login')
  await page.evaluate(({ token, user }) => {
    localStorage.setItem('token', token)
    localStorage.setItem('user', JSON.stringify({
      username: user.username,
      role: user.role,
      department: user.department,
    }))
  }, { token, user })

  // Save storageState for other tests to reuse
  await page.context().storageState({ path: authFile })
})
