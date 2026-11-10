import { expect, test } from '../browserFixtures'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { findSessionRow } from '../browserSessionNavigation'

const captures = resolve(process.env.EUROPA_CAPTURE_ROOT ? `${process.env.EUROPA_CAPTURE_ROOT}/sessions` : '../docs/screenshots/phase3h/final/sessions')
test('persisted mixed recents and real Idle, Busy, Idle, Stopped sessions', async ({ page, request }) => {
  mkdirSync(captures, { recursive: true })
  const capture = async (name: string) => {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: join(captures, name + '.png'), fullPage: true, animations: 'disabled' })
  }
  const catalog = await request.post('/api/notebooks', { data: { title: 'Catalog analysis' } })
  expect(catalog.status()).toBe(201)
  const catalogId = (await catalog.json()).metadata.europa.notebook_id
  await page.goto(`/notebooks/${catalogId}`)
  await expect(page.locator('footer.status')).toContainText('Document saved')
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await page.getByRole('button', { name: 'Existing', exact: true }).click()
  await page.getByRole('button', { name: 'Imported.ipynb', exact: true }).click()
  await expect(page.locator('.cell .cm-content')).toContainText('print(42)')
  const physicalId = new URL(page.url()).pathname.split('/').at(-1)!
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect.poll(async () => (await (await request.get('/api/dashboard/recent')).json()).items.slice(0, 2).map((item: {id: string}) => item.id)).toEqual([physicalId, catalogId])
  await page.getByRole('button', { name: 'Recent notebooks' }).click()
  await expect(page.getByRole('menuitem', { name: /Imported/ })).toContainText('Home / Existing')
  await expect(page.getByRole('menuitem', { name: /Catalog analysis/ })).toContainText('Private catalog')
  await capture('recent-real')
  await page.getByRole('menuitem', { name: /Catalog analysis/ }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${catalogId}$`))
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect.poll(async () => (await (await request.get('/api/dashboard/recent')).json()).items.slice(0, 2).map((item: {id: string}) => item.id)).toEqual([catalogId, physicalId])
  await page.reload()
  await page.getByRole('button', { name: 'Recent notebooks' }).click()
  await expect(page.getByRole('menuitem', { name: /Catalog analysis/ })).toBeVisible()
  await page.keyboard.press('Escape')
  expect((await (await request.get(`/api/notebooks/${physicalId}/session`)).json()).kernels).toBe(0)
  const kernel = await request.post(`/api/kernel/start?notebook_id=${physicalId}`)
  expect(kernel.status()).toBe(201)
  const row = await findSessionRow(page, 'Imported')
  await expect(row.getByRole('cell', { name: 'Idle', exact: true })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Shut down' })).toBeDisabled()
  await capture('running-idle-real')
  const release = join(process.env.EUROPA_B_WORKSPACE!, '.release-c-execution')
  const document = await (await request.get(`/api/notebooks/${physicalId}`)).json()
  const cell = document.cells[0].id
  const source = `from pathlib import Path\nfrom threading import Event\nwhile not Path(${JSON.stringify(release)}).exists():\n    Event().wait(0.05)\nprint(42)`
  const edited = await request.patch(`/api/notebooks/${physicalId}/cells/${cell}`, { data: { revision: document.metadata.europa.revision, source } })
  expect(edited.status()).toBe(200)
  const execute = await request.post(`/api/notebooks/${physicalId}/cells/${cell}/execute`, { data: { revision: (await edited.json()).metadata.europa.revision } })
  expect(execute.status()).toBe(202)
  const executionId = (await execute.json()).execution_id
  const status = async () => {
    let cursor: string | null = null
    do {
      const data = await (await request.get(`/api/dashboard/sessions${cursor ? `?cursor=${cursor}` : ''}`)).json()
      const item = data.items.find((item: { id: string }) => item.id === physicalId)
      if (item) return item.state
      cursor = data.next_cursor
    } while (cursor)
    return 'Missing'
  }
  try {
    await expect.poll(status).toBe('Busy')
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(row.getByRole('cell', { name: 'Busy', exact: true })).toBeVisible()
    await capture('running-busy-real')
  } finally { writeFileSync(release, 'release') }
  await expect.poll(async () => (await (await request.get(`/api/notebooks/${physicalId}/executions/${executionId}`)).json()).state).toBe('applied')
  await expect.poll(status).toBe('Idle')
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(row.getByRole('cell', { name: 'Idle', exact: true })).toBeVisible()
  await page.route('**/api/dashboard/sessions*', route => route.abort('failed'))
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(row.getByRole('cell', { name: 'Unknown', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('observation unavailable')
  await capture('running-failed-refresh')
  await page.unroute('**/api/dashboard/sessions*')
  expect((await request.delete(`/api/runtime?notebook_id=${physicalId}`)).status()).toBe(204)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(row.getByRole('cell', { name: 'Stopped', exact: true })).toBeVisible()
  await capture('running-stopped-real')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await capture('running-mobile-real')
  await row.getByRole('button', { name: 'Imported', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${physicalId}$`))
  await expect(page.locator('.cell-output')).toContainText('42')
  await expect(page.locator('footer.status')).toContainText('No pending receipt')
})
