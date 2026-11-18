import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端 FastAPI 服务地址：默认 8000，可用环境变量覆盖。
// 为什么做成可配：本机 8000 / 5173 可能被其他项目占用（实测踩过），
// 此时需要换端口启动而不必改源码。
const BACKEND_TARGET = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
const DEV_PORT = Number(process.env.VITE_DEV_PORT || 5173)

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: DEV_PORT,
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
