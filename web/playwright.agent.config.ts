import { defineConfig } from '@playwright/test'
import { mkdirSync, mkdtempSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// Phase 4B Home Coordinator journeys. Separate from playwright.config.ts: its own disposable
// roots, its own loopback port and the deterministic demo provider (not an LLM). The config is
// re-evaluated in the worker, so reuse the root chosen by the main process.
const root = process.env.EUROPA_AGENT_ROOT || mkdtempSync(join(realpathSync(tmpdir()), 'europa-agent-'))
const workspace = join(root, 'workspace')
const port = process.env.EUROPA_AGENT_PORT || '18400'
mkdirSync(workspace, { recursive: true })
Object.assign(process.env, { EUROPA_AGENT_ROOT: root, EUROPA_AGENT_PORT: port, EUROPA_AGENT_WORKSPACE: workspace })

export default defineConfig({
  testDir: './browser-agent', workers: 1, retries: 0, timeout: 90000, preserveOutput: 'always',
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true, viewport: { width: 1360, height: 900 }, actionTimeout: 10000,
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.EUROPA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' } },
  webServer: {
    // Serve the current build from the API process; `exec` keeps uvicorn as the process Playwright stops.
    command: `npm run build && exec ../.venv/bin/python -m uvicorn main:app --app-dir ../api/src --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}/`, reuseExistingServer: false, timeout: 90000,
    // SIGTERM (not Playwright's default SIGKILL) lets uvicorn run lifespan shutdown, which stops the
    // local Gateway it started in its own process group; a SIGKILL would orphan that Gateway.
    gracefulShutdown: { signal: 'SIGTERM', timeout: 15000 },
    env: { EUROPA_WORKSPACE_ROOT: workspace, EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks'), EUROPA_APPLICATION_ROOT: join(root, 'application'),
      EUROPA_RUNTIME_REGISTRY: join(root, 'runtime.sqlite3'), EUROPA_RUNTIME_PROVIDER: 'local', EUROPA_GATEWAY_URL: '', EUROPA_GATEWAY_TOKEN: '',
      EUROPA_AGENT_PROVIDER: 'demo', EUROPA_AGENT_MODEL: '', EUROPA_AGENT_BASE_URL: '', EUROPA_AGENT_API_KEY: '', OPENAI_API_KEY: '', OPENROUTER_API_KEY: '' },
  },
})
