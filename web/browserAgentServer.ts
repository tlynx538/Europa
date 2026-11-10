import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, realpathSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { expect, type APIRequestContext } from './browserFixtures'

/** Dedicated disposable process and roots for real restart journeys. */
export function agentServer(request: APIRequestContext) {
  const root = mkdtempSync(join(realpathSync(tmpdir()), 'europa-agent-browser-'))
  const workspace = join(root, 'workspace'); mkdirSync(workspace)
  const url = 'http://127.0.0.1:18102'
  let owned: ChildProcess | undefined
  let errors = ''
  async function start() {
    errors = ''
    owned = spawn(resolve('../.venv/bin/python'), ['-m','uvicorn','main:app','--app-dir',resolve('../api/src'),'--host','127.0.0.1','--port','18102'], {
      env: { ...process.env, EUROPA_WORKSPACE_ROOT: workspace, EUROPA_NOTEBOOK_ROOT: join(root,'notebooks'), EUROPA_APPLICATION_ROOT: join(root,'application'), EUROPA_RUNTIME_PROVIDER: 'local', EUROPA_GATEWAY_URL: '', EUROPA_GATEWAY_TOKEN: '', EUROPA_AGENT_PROVIDER: 'fake', EUROPA_AGENT_MODEL: 'fake-v1', EUROPA_AGENT_API_KEY: '' },
      stdio: ['ignore','ignore','pipe'],
    })
    owned.stderr?.on('data', chunk => { errors = (errors + chunk.toString()).slice(-5000) })
    await expect.poll(async () => {
      if (owned?.exitCode !== null) throw new Error(`Disposable agent backend failed: ${errors}`)
      try { const response=await request.get(url+'/api/agents/configuration'); return response.ok() && (await response.json()).test_provider === true }
      catch { return false }
    }, { timeout: 30000 }).toBe(true)
  }
  async function stop() {
    if (!owned || owned.exitCode !== null || owned.signalCode !== null) return
    const process = owned
    const exit = new Promise<void>((resolve, reject) => {
      process.once('exit',(code,signal) => code===0 || signal==='SIGTERM' ? resolve() : reject(new Error(`Backend exited ${code}/${signal}: ${errors}`)))
      process.once('error',reject)
    })
    process.kill('SIGTERM'); await exit
  }
  return { root, workspace, url, start, stop }
}
