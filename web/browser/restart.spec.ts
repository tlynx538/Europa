import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

test('production UI recovers after an actual backend process restart', async ({ page, request }, info) => {
  const root = mkdtempSync(join(process.env.EUROPA_TEST_TMP || tmpdir(), 'europa-restart-'))
  const url = 'http://127.0.0.1:18100'
  let backend: ChildProcess | undefined
  const start = async () => {
    backend = spawn(resolve('../.venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--app-dir', resolve('../api/src'), '--host', '127.0.0.1', '--port', '18100'], {
      env: { ...process.env, EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks'), EUROPA_APPLICATION_ROOT: join(root, 'application'), EUROPA_RUNTIME_PROVIDER: 'local', EUROPA_GATEWAY_URL: '', EUROPA_GATEWAY_TOKEN: '' }, stdio: 'ignore',
    })
    await expect.poll(async () => { if (backend?.exitCode !== null) return false; try { return (await request.get(`${url}/api/notebooks`)).ok() } catch { return false } }, { timeout: 30000 }).toBe(true)
  }
  const stop = async () => {
    const process = backend
    if (!process || process.exitCode !== null || process.signalCode !== null) return
    const exited = new Promise<void>((resolve, reject) => { process.once('exit', (code, signal) => code === 0 || signal === 'SIGTERM' ? resolve() : reject(new Error(`Backend exit ${code}/${signal}`))); process.once('error', reject) })
    process.kill('SIGTERM'); await exited
  }
  try {
    await start()
    const response = await request.get(url)
    expect(response.headers()['content-security-policy']).toContain("script-src 'self'")
    const doc = await (await request.post(`${url}/api/notebooks`, { data: { title: 'Restart proof' } })).json()
    const id = doc.metadata.europa.notebook_id
    await request.post(`${url}/api/notebooks/${id}/cells`, { data: { revision: 1, source: 'print(123)' } })
    const project = await (await request.post(`${url}/api/projects`, { data: { operation_id: crypto.randomUUID(), title: 'Restart project' } })).json()
    await request.post(`${url}/api/projects/${project.id}/notebooks`, { data: { operation_id: crypto.randomUUID(), revision: 1, notebook_id: id } })
    const conversation = await (await request.post(`${url}/api/conversations`, { data: { operation_id: crypto.randomUUID(), project_id: project.id } })).json()
    await request.post(`${url}/api/conversations/${conversation.id}/messages`, { data: { operation_id: crypto.randomUUID(), content: 'Survives backend restart' } })
    await page.goto(url)
    await page.getByLabel('Notebook', { exact: true }).selectOption(id)
    await page.getByLabel('Project', { exact: true }).selectOption(project.id)
    await page.getByRole('button', { name: 'Run', exact: true }).click()
    await expect(page.locator('.cell .outputs')).toContainText('123', { timeout: 45000 })
    await stop()
    await expect(page.getByRole('status')).toContainText('reconnecting', { timeout: 10000 })
    await start()
    await expect(page.getByRole('status')).toContainText('API: connected', { timeout: 15000 })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Restart proof' })).toBeVisible()
    await expect(page.locator('.cell .outputs')).toContainText('123')
    await expect(page.getByRole('status')).toContainText('Kernel: disconnected')
    await expect(page.getByText('Survives backend restart', { exact: true })).toBeVisible()
    await page.screenshot({ path: info.outputPath('restart.png'), fullPage: true })
  } finally { await stop() }
})
