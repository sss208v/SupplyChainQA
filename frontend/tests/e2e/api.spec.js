import { test, expect } from '@playwright/test'

test.describe('API 接口测试', () => {
  const API = 'http://127.0.0.1:8001'

  test('健康检查', async ({ request }) => {
    const res = await request.get(`${API}/health`)
    expect(res.ok()).toBeTruthy()
  })

  test('配置接口', async ({ request }) => {
    const res = await request.get(`${API}/config`)
    expect(res.ok()).toBeTruthy()
  })

  test('管理员登录成功', async ({ request }) => {
    const res = await request.post(`${API}/api/v1/auth/login`, {
      data: { username: 'admin', password: 'admin123' },
    })
    expect(res.ok()).toBeTruthy()
    const data = await res.json()
    expect(data.token).toBeTruthy()
    expect(data.user.username).toBe('admin')
  })

  test('错误密码登录失败', async ({ request }) => {
    const res = await request.post(`${API}/api/v1/auth/login`, {
      data: { username: 'admin', password: 'wrong' },
    })
    expect(res.status()).toBeGreaterThanOrEqual(400)
  })

  test('带 token 访问知识库', async ({ request }) => {
    const login = await request.post(`${API}/api/v1/auth/login`, {
      data: { username: 'admin', password: 'admin123' },
    })
    const { token } = await login.json()
    const res = await request.get(`${API}/api/v1/knowledge/list`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(res.ok()).toBeTruthy()
  })

  test('带 token 访问工具列表', async ({ request }) => {
    const login = await request.post(`${API}/api/v1/auth/login`, {
      data: { username: 'admin', password: 'admin123' },
    })
    const { token } = await login.json()
    const res = await request.get(`${API}/api/v1/tools/list`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(res.ok()).toBeTruthy()
  })
})
