// Oberflaechen-Tests (npm run test:e2e): Playwright steuert Electron, kein
// eigener Browser noetig. Backend und Oberflaeche startet global-setup.mjs.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: /.*\.spec\.mjs/,
  globalSetup: './tests/e2e/global-setup.mjs',
  timeout: 120000,
  workers: 1,
  reporter: [['list']],
})
