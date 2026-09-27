import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { readFileSync } from 'node:fs'

// Version in der Titelleiste aus package.json (stand bis 1.5.1 fest auf 1.4.0)
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))

export default defineConfig({
  plugins: [svelte({
    onwarn(warning, handler) {
      // Suppress A11y warnings that are intentional (drag handles, separators)
      if (warning.code.startsWith('a11y_')) return
      handler(warning)
    }
  })],
  base: './',
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: {
    port: 5173
  },
  build: {
    outDir: 'dist'
  }
})
