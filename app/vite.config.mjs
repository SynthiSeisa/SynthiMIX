import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte({
    onwarn(warning, handler) {
      // Suppress A11y warnings that are intentional (drag handles, separators)
      if (warning.code.startsWith('a11y_')) return
      handler(warning)
    }
  })],
  base: './',
  server: {
    port: 5173
  },
  build: {
    outDir: 'dist'
  }
})
