/**
 * API 请求层测试
 *
 * 测试范围：
 * - 请求拦截器：自动添加 Authorization header
 * - 响应拦截器：401 处理、错误消息提取
 * - 基本配置正确性
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

// Mock localStorage
const localStorageMock = (() => {
  let store = {}
  return {
    getItem: vi.fn((key) => store[key] || null),
    setItem: vi.fn((key, value) => { store[key] = value }),
    removeItem: vi.fn((key) => { delete store[key] }),
    clear: vi.fn(() => { store = {} }),
  }
})()
Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock })

// Mock window.location
const originalLocation = window.location
delete window.location
window.location = { href: '' }

describe('API 请求层 (request.js)', () => {
  let request

  beforeEach(async () => {
    // 清除模块缓存，确保每个测试获取独立实例
    vi.resetModules()
    localStorageMock.clear()
    window.location.href = ''

    // 动态导入以获取新的 axios 实例
    const mod = await import('@/api/request')
    request = mod.default
  })

  describe('请求拦截器', () => {
    it('应为 axios 实例', () => {
      expect(request).toBeDefined()
      expect(typeof request.get).toBe('function')
      expect(typeof request.post).toBe('function')
    })

    it('有 token 时应添加 Authorization header', () => {
      localStorageMock.getItem.mockImplementation((key) => {
        if (key === 'token') return 'test-token-123'
        return null
      })

      // 直接调用拦截器：通过 config.transformRequest 验证
      const config = {
        headers: {},
        url: '/api/test',
        method: 'get',
      }

      // 从 axios 实例获取 request 拦截器并执行
      const interceptors = request.interceptors.request.handlers
      expect(interceptors.length).toBeGreaterThan(0)

      const fulfilled = interceptors[0].fulfilled
      const result = fulfilled(config)

      expect(result.headers.Authorization).toBe('Bearer test-token-123')
    })

    it('无 token 时不应添加 Authorization header', () => {
      localStorageMock.getItem.mockReturnValue(null)

      const config = {
        headers: {},
        url: '/api/test',
        method: 'get',
      }

      const interceptors = request.interceptors.request.handlers
      const fulfilled = interceptors[0].fulfilled
      const result = fulfilled(config)

      expect(result.headers.Authorization).toBeUndefined()
    })

    it('请求错误应被正确拒绝', async () => {
      const interceptors = request.interceptors.request.handlers
      const rejected = interceptors[0].rejected

      const error = new Error('网络错误')
      await expect(rejected(error)).rejects.toThrow('网络错误')
    })
  })

  describe('响应拦截器', () => {
    it('成功响应应返回 response.data', () => {
      const interceptors = request.interceptors.response.handlers
      const fulfilled = interceptors[0].fulfilled

      const response = {
        data: { success: true, items: [1, 2, 3] },
        status: 200,
      }

      const result = fulfilled(response)
      expect(result).toEqual({ success: true, items: [1, 2, 3] })
    })

    it('401 错误应清除 token 并跳转登录页', async () => {
      const interceptors = request.interceptors.response.handlers
      const rejected = interceptors[0].rejected

      const error = {
        response: {
          status: 401,
          data: { detail: 'Unauthorized' },
        },
        message: 'Request failed with status code 401',
      }

      try {
        await rejected(error)
      } catch {
        // 预期抛出
      }

      expect(localStorageMock.removeItem).toHaveBeenCalledWith('token')
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('user')
      expect(window.location.href).toBe('/login')
    })

    it('非 401 错误应提取 detail 错误信息', async () => {
      const interceptors = request.interceptors.response.handlers
      const rejected = interceptors[0].rejected

      const error = {
        response: {
          status: 500,
          data: { detail: '服务器内部错误' },
        },
        message: 'Request failed with status code 500',
      }

      try {
        await rejected(error)
      } catch (e) {
        expect(e.message).toBe('服务器内部错误')
      }
    })

    it('无 response 时应使用 error.message', async () => {
      const interceptors = request.interceptors.response.handlers
      const rejected = interceptors[0].rejected

      const error = {
        response: undefined,
        message: 'Network Error',
      }

      try {
        await rejected(error)
      } catch (e) {
        expect(e.message).toBe('Network Error')
      }
    })
  })

  describe('基本配置', () => {
    it('超时时间应为 30000ms', () => {
      expect(request.defaults.timeout).toBe(30000)
    })

    it('默认 Content-Type 应为 application/json', () => {
      expect(request.defaults.headers['Content-Type']).toBe('application/json')
    })
  })
})
