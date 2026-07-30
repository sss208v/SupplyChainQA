import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 1200, // element-plus 单体包 ~1MB，为已知大库，不触发 warning
    rollupOptions: {
      output: {
        // 产物文件名必须含 content hash：nginx 对静态资源设了 1 年 immutable 强缓存（L4），
        // 依赖"内容变更即改名"保证发版后浏览器拉到新文件（vite 默认即此格式，此处显式固化）
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
        manualChunks(id) {
          if (id.includes('node_modules/element-plus')) return 'element-plus'
          if (id.includes('node_modules')) return 'vendor'
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: true,  // 允许局域网访问（手机演示用）
    port: 5173,
    allowedHosts: [
      '.ngrok-free.dev',
      '.ngrok-free.app',
      '.ngrok.io',
      'localhost',
    ],
    proxy: {
      // 健康检查接口直接代理到后端
      '/health': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
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
