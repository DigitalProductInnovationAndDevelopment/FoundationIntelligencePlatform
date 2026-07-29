import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // The compressed-size gate is stricter and distinguishes initial from
    // deferred code. This raw-size ceiling keeps Vite's reporting aligned.
    chunkSizeWarningLimit: 1300,
  },
})
