import { defineConfig } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(process.env.EUROPA_TEST_TMP || tmpdir(), 'europa-web-test-'))
export default defineConfig({
  testDir: './browser', timeout: 90000, workers: 1, preserveOutput: 'always',
  use: { baseURL: 'http://127.0.0.1:15173', headless: true,
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  webServer: [
    { command: '../.venv/bin/python -m uvicorn main:app --app-dir ../api/src --host 127.0.0.1 --port 18000',
      url: 'http://127.0.0.1:18000/', reuseExistingServer: false,
      env: { EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks') }, timeout: 30000 },
    { command: 'npm run dev -- --port 15173 --strictPort', url: 'http://127.0.0.1:15173/', reuseExistingServer: false,
      env: { EUROPA_API_PROXY: 'http://127.0.0.1:18000' } },
  ],
})
