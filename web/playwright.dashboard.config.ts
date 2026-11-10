import { defineConfig } from '@playwright/test'

// Static composition only: no API, Gateway, or owner's workspace is launched.
export default defineConfig({
  testDir: './browser-dashboard', workers: 1, retries: 0, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:18173', viewport: { width: 1440, height: 900 }, headless: true,
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  webServer: { command: 'npm run dev -- --port 18173 --strictPort', url: 'http://127.0.0.1:18173/dashboard-preview.html', reuseExistingServer: false },
})
