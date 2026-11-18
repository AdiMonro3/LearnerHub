import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Must match FRONTEND_URL in backend/.env (CORS + where Google sign-in returns to).
  server: { host: 'localhost', port: 5173, strictPort: true },
})
