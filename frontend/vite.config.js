import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    allowedHosts: [
      '.ngrok-free.dev',
      '.ngrok-free.app',
      '.ngrok.io',
      'localhost',
    ],
    proxy: {
      // 代理API请求到后端FastAPI，避免CORS问题
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        // SSE 流式传输必须禁用代理缓冲
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            // 保持 SSE 连接
            if (req.headers.accept === 'text/event-stream') {
              proxyReq.setHeader('Cache-Control', 'no-cache')
              proxyReq.setHeader('Connection', 'keep-alive')
            }
          })
          proxy.on('proxyRes', (proxyRes, req, res) => {
            // 对 SSE 请求禁用缓冲
            if (req.headers.accept === 'text/event-stream') {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
              // 关键：告诉代理不要缓冲响应
              res.setHeader('Cache-Control', 'no-cache')
              res.setHeader('X-Accel-Buffering', 'no')
            }
          })
        },
      },
    },
  },
})
