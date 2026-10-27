import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: { '/api': { target: process.env.EUROPA_API_PROXY || 'http://127.0.0.1:8000', changeOrigin: false } } },
  build: { rollupOptions: { output: { manualChunks(id) {
    if (/node_modules\/(?:@codemirror|@lezer|codemirror|@uiw)/.test(id)) return 'editor'
    if (/node_modules\/(?:react|react-dom|scheduler)\//.test(id)) return 'react'
    if (id.includes('node_modules')) return 'libraries'
  } } } },
})
