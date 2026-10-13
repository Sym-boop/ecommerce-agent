import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端 FastAPI 服务地址（单一事实来源）：切换后端端口只需改这一处
const BACKEND_TARGET = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 前端统一使用相对路径（/api、/ws、/media），由 proxy 转发到后端
    proxy: {
      '/api': {
        target: BACKEND_TARGET,
        changeOrigin: true,
      },
      '/ws': {
        target: BACKEND_TARGET.replace('http', 'ws'),
        ws: true,
        changeOrigin: true,
      },
      '/media': {
        target: BACKEND_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2018',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})
