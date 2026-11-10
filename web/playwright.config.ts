import { defineConfig } from '@playwright/test'
import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// All supported browser journeys run against one accumulated disposable catalog.
// Reuse this root when Playwright evaluates config in a worker, never owner data.
const fresh = !process.env.EUROPA_ACCEPTANCE_ROOT
const root = process.env.EUROPA_ACCEPTANCE_ROOT || mkdtempSync(join(realpathSync(tmpdir()), 'europa-acceptance-'))
const workspace = join(root, 'workspace')
const port = process.env.EUROPA_TEST_PORT || '18000'
Object.assign(process.env, {
  EUROPA_ACCEPTANCE_ROOT: root, EUROPA_B_WORKSPACE: workspace, EUROPA_TEST_TMP: root,
  EUROPA_WORKSPACE_ROOT: workspace, EUROPA_TEST_PORT: port,
  EUROPA_CAPTURE_ROOT: process.env.EUROPA_CAPTURE_ROOT || resolve('../docs/screenshots/phase3h/final'),
})
if (fresh) {
  mkdirSync(join(workspace, 'Existing'), { recursive: true })
  mkdirSync(join(workspace, 'Long filenames'))
  writeFileSync(join(workspace, 'Existing/Imported.ipynb'), JSON.stringify({ nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [{ id: 'source', cell_type: 'code', source: 'print(42)', metadata: {}, outputs: [], execution_count: null }] }))
  writeFileSync(join(workspace, 'Existing/README.md'), '# Workspace fixture\n')
  writeFileSync(join(workspace, 'Existing/data.parquet'), 'unsupported fixture')
  writeFileSync(join(workspace, 'Long filenames', 'Customer_revenue_analysis_with_detailed_observations_and_multiple_regional_comparisons_for_the_complete_financial_year_and_followup_notes.md'), '# Long filename fixture\n')
}
export default defineConfig({
  timeout: 90000, workers: 1, retries: 0, preserveOutput: 'always',
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true, screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  projects: [
    { name: 'application', testDir: './browser' },
    { name: 'workspace', testDir: './browser-workspace' },
    { name: 'sessions', testDir: './browser-dashboard-c' },
    { name: 'composition', testDir: './browser-dashboard', use: { baseURL: 'http://127.0.0.1:18173' } },
    { name: 'acceptance', testDir: './browser-acceptance' },
  ],
  webServer: process.env.EUROPA_VM_ONLY === '1' ? [] : [
    { command: `../.venv/bin/python -m uvicorn main:app --app-dir ../api/src --host 127.0.0.1 --port ${port}`,
      url: `http://127.0.0.1:${port}/`, reuseExistingServer: false,
      env: { EUROPA_WORKSPACE_ROOT: workspace, EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks'), EUROPA_APPLICATION_ROOT: join(root, 'application'), EUROPA_RUNTIME_PROVIDER: 'local', EUROPA_GATEWAY_URL: '', EUROPA_GATEWAY_TOKEN: '' }, timeout: 30000 },
    { command: 'npm run dev -- --port 18173 --strictPort', url: 'http://127.0.0.1:18173', reuseExistingServer: false },
  ],
})
