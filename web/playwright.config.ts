import { defineConfig } from '@playwright/test'
import { mkdtempSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(realpathSync(process.env.EUROPA_TEST_TMP || tmpdir()), 'europa-web-test-'))
const port = process.env.EUROPA_TEST_PORT || '18000'
export default defineConfig({
  testDir: './browser', timeout: 90000, workers: 1, preserveOutput: 'always',
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true,
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  webServer: process.env.EUROPA_VM_ONLY === '1' ? [] : [
    { command: `../.venv/bin/python -m uvicorn main:app --app-dir ../api/src --host 127.0.0.1 --port ${port}`,
      url: `http://127.0.0.1:${port}/`, reuseExistingServer: false,
      env: { EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks'), EUROPA_RUNTIME_PROVIDER: 'local' }, timeout: 30000 },
  ],
})
