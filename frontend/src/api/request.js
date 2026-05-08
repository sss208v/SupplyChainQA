/**
 * SmartQA Pro - Axios 请求封装
 *
 * 1. 创建 axios 实例，统一配置 baseURL、超时时间、请求/响应拦截器
 * 2. 请求拦截器：可以自动添加 token 等认证信息
 * 3. 响应拦截器：统一处理错误码（401跳登录、500提示错误等）
 */
import axios from 'axios'

const request = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 响应拦截器
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const msg = error.response?.data?.detail || error.message || '请求失败'
    console.error('API Error:', msg)
    return Promise.reject(new Error(msg))
  }
)

export default request
