import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 개발 서버는 /api를 로컬 FastAPI(uvicorn coffee.api:app)로 넘긴다. 배포에서는 nginx가 같은 일을 한다.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
