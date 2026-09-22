import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发: 9101 端口, /api 代理到本机枢纽后端 9100
// 生产: npm run build → dist/ 由枢纽后端 StaticFiles 同端口分发 (免 CORS)
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 9101,
    proxy: {
      '/api': {
        target: process.env.HUB_PROXY_TARGET || 'http://localhost:9100',
        changeOrigin: true,
      },
    },
  },
})
