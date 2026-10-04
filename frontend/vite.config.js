import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 개발 서버는 /api를 로컬 FastAPI(uvicorn coffee.api:app)로 넘긴다. 배포에서는 nginx가 같은 일을 한다.
// 로컬 DB가 없을 때는 API_TARGET에 운영 주소를 넣어 읽기 전용 API로 화면을 확인할 수 있다.
const target = process.env.API_TARGET ?? 'http://127.0.0.1:8000'
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': { target, changeOrigin: true },
      '/health': { target, changeOrigin: true },
    },
  },
})
