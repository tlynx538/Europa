import { test, expect } from '../browserFixtures'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, realpathSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'

test('physical identity, project, output, receipts and recents survive an actual backend process restart', async ({ page, request }, info) => {
  const root = mkdtempSync(join(realpathSync(tmpdir()), 'europa-physical-restart-'))
  const workspace = join(root, 'workspace'); mkdirSync(workspace)
  const url = 'http://127.0.0.1:18101'
  let backend: ChildProcess | undefined
  let errors = ''
  const start = async () => {
    backend = spawn(resolve('../.venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--app-dir', resolve('../api/src'), '--host', '127.0.0.1', '--port', '18101'], { env: { ...process.env, EUROPA_WORKSPACE_ROOT: workspace, EUROPA_NOTEBOOK_ROOT: join(root, 'notebooks'), EUROPA_APPLICATION_ROOT: join(root, 'application'), EUROPA_RUNTIME_PROVIDER: 'local', EUROPA_GATEWAY_URL: '', EUROPA_GATEWAY_TOKEN: '' }, stdio: ['ignore', 'ignore', 'pipe'] })
    backend.stderr?.on('data', data => { errors = (errors + data.toString()).slice(-4000) })
    await expect.poll(async () => { if (backend?.exitCode !== null) throw new Error(errors); try { return (await request.get(url + '/api/notebooks')).ok() } catch { return false } }, { timeout: 30000 }).toBe(true)
  }
  const stop = async () => {
    if (!backend || backend.exitCode !== null || backend.signalCode !== null) return
    const owned = backend
    const exit = new Promise<void>((resolve, reject) => { owned.once('exit', (code, signal) => code === 0 || signal === 'SIGTERM' ? resolve() : reject(new Error(`Backend exit ${code}/${signal}`))); owned.once('error', reject) })
    owned.kill('SIGTERM'); await exit
  }
  try {
    await start()
    await page.goto(url)
    const emptyObserved = page.waitForResponse(response => response.url().endsWith('/api/dashboard/sessions'))
    await page.getByRole('tab', { name: 'Running', exact: true }).click()
    await emptyObserved
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
    await expect(page.getByText('No active notebook sessions.', { exact: true })).toBeVisible()
    const captures = join(process.env.EUROPA_CAPTURE_ROOT!, 'acceptance'); mkdirSync(captures, { recursive: true })
    await page.screenshot({ path: join(captures, 'empty-running-real.png'), fullPage: true })
    const project = await (await request.post(url + '/api/workspace/projects', { data: { path: 'Restart Project', operation_id: crypto.randomUUID() } })).json()
    const id = (await (await request.post(url + '/api/workspace/notebooks', { data: { path: 'Restart Project/Saved.ipynb', operation_id: crypto.randomUUID() } })).json()).notebook_id
    await request.post(`${url}/api/notebooks/${id}/cells`, { data: { revision: 1, source: 'print(73)' } })
    await page.goto(`${url}/notebooks/${id}`)
    await page.locator('.cm-content').click(); await page.getByRole('button', { name: 'Run selected cell', exact: true }).click()
    await expect(page.locator('.outputs')).toContainText('73')
    await expect(page.locator('footer.status')).toContainText('No pending receipt')
    const saved = await (await request.get(`${url}/api/notebooks/${id}`)).json()
    const conversation = await (await request.get(`${url}/api/notebooks/${id}/conversation`)).json()
    await page.getByRole('link', { name: 'Europa', exact: true }).click()
    const recent = await (await request.get(url + '/api/dashboard/recent')).json()
    await stop()
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.getByRole('alert').first()).toBeVisible()
    await start()
    expect(await (await request.get(url + '/api/dashboard/recent')).json()).toEqual(recent)
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await page.getByRole('button', { name: 'Restart Project Project', exact: true }).click()
    await page.getByRole('button', { name: 'Saved.ipynb', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
    await expect(page.locator('.outputs')).toContainText('73')
    expect(await (await request.get(`${url}/api/notebooks/${id}`)).json()).toEqual(saved)
    expect((await (await request.get(`${url}/api/notebooks/${id}/conversation`)).json()).id).toBe(conversation.id)
    const reopened = await (await request.get(url + '/api/dashboard/recent')).json()
    expect(reopened.items.map((item: {id: string}) => item.id)).toEqual(recent.items.map((item: {id: string}) => item.id))
    const membership = await (await request.get(`${url}/api/projects/${project.id}`)).json()
    expect(membership.data.notebooks).toEqual([{ notebook_id: id, present: true }])
    await info.attach('restart-contract', { body: JSON.stringify({ sameNotebook: true, sameConversation: true, revision: saved.metadata.europa.revision, projectVerified: true, recentPersisted: true }), contentType: 'application/json' })
  } finally { await stop() }
})
