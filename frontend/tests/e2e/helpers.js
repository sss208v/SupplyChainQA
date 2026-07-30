// Helpers - tests in the 'chromium' project already have auth via storageState
export async function clearAuth(page) {
  await page.goto('/login')
  await page.evaluate(() => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  })
}

export const API_BASE = 'http://127.0.0.1:8001'
