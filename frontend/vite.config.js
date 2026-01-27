import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export default defineConfig({
  plugins: [vue()],
  // 生产环境使用相对路径，支持 Electron file:// 协议
  base: './',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // 生成 sourcemap 便于调试
    sourcemap: false,
    // 输出目录
    outDir: 'dist',
    // 静态资源目录
    assetsDir: 'assets',
    // 关闭文件大小警告
    chunkSizeWarningLimit: 2000,
  },
  server: {
    port: 6001,
    proxy: {
      // API 代理
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // WebSocket 代理
      '/ws': {
        target: 'ws://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
      // 视频流代理
      '/video_feed': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // 上传文件访问代理
      '/uploads': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    }
  }
});
