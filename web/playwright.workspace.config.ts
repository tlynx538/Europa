import { defineConfig } from '@playwright/test'
import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const fixture = process.env.EUROPA_B_FIXTURE || mkdtempSync(join(realpathSync(tmpdir()), 'europa-checkpoint-b-'))
const seed = !process.env.EUROPA_B_FIXTURE
process.env.EUROPA_B_FIXTURE = fixture
const workspace = join(fixture, 'workspace')
process.env.EUROPA_B_WORKSPACE = workspace
if (seed) {
mkdirSync(join(workspace, 'Existing'), { recursive: true })
writeFileSync(join(workspace, 'Existing', 'Imported.ipynb'), JSON.stringify({ nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [{ id: 'source', cell_type: 'code', source: 'print(42)', metadata: {}, outputs: [], execution_count: null }] }))
writeFileSync(join(workspace, 'Existing', 'README.md'), '# Workspace fixture\n')
writeFileSync(join(workspace, 'Existing', 'data.parquet'), 'unsupported fixture')
}
export default defineConfig({
  testDir: './browser-workspace', workers: 1, retries: 0, timeout: 90000,
  use: { actionTimeout: 10000, screenshot: 'only-on-failure', trace: 'retain-on-failure', baseURL: 'http://127.0.0.1:18018', viewport: { width: 1440, height: 900 }, headless: true,
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  webServer: { command: '../.venv/bin/python -m uvicorn main:app --app-dir ../api/src --host 127.0.0.1 --port 18018', url: 'http://127.0.0.1:18018/', reuseExistingServer: false, timeout: 30000,
    env: { EUROPA_WORKSPACE_ROOT: workspace, EUROPA_NOTEBOOK_ROOT: join(fixture, 'notebooks'), EUROPA_APPLICATION_ROOT: join(fixture, 'application'), EUROPA_RUNTIME_PROVIDER: 'local' } },
})
