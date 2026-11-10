import { test, expect } from '../browserFixtures'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

async function seed(request: import('@playwright/test').APIRequestContext, title: string, source = 'print(1)') {
  const path = `Existing/${title}.ipynb`
  const response = await request.post('/api/workspace/notebooks', { data: { path, operation_id: crypto.randomUUID() } }); expect(response.status()).toBe(201)
  const id = (await response.json()).notebook_id
  const cell = await (await request.post(`/api/notebooks/${id}/cells`, { data: { revision: 1, source } })).json()
  return { id, cell: cell.cells[0].id, path }
}
async function disableAutosave(page: import('@playwright/test').Page) {
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: /Autosave/ }).click()
}

test('browser Back guards drafts, external edits cannot overwrite, and restored file reopens with its draft', async ({ page, request }) => {
  const { id, path } = await seed(request, 'External conflict')
  const disk = join(process.env.EUROPA_B_WORKSPACE!, path)
  const original = readFileSync(disk)
  await page.goto('/browse?path=Existing')
  await page.getByRole('button', { name: 'External conflict.ipynb', exact: true }).click()
  await expect(page.locator('.cm-content')).toContainText('print(1)')
  await expect(page.locator('footer.status')).toContainText('API: connected')
  await disableAutosave(page)
  const editor = page.locator('.cm-content')
  await editor.click(); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.insertText('print(99)')
  page.once('dialog', dialog => dialog.dismiss())
  await page.goBack()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
  await expect(editor).toContainText('print(99)')
  const external = JSON.parse(original.toString()); external.cells[0].source = 'outside edit'
  writeFileSync(disk, JSON.stringify(external))
  await page.getByRole('button', { name: 'Save notebook', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Revision conflict')
  expect(JSON.parse(readFileSync(disk, 'utf8')).cells[0].source).toBe('outside edit')
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  expect(await page.evaluate(id => localStorage.getItem(`europa.drafts.${id}`), id)).toContain('print(99)')
  const recent = await (await request.get('/api/dashboard/recent')).json()
  expect(recent.items.find((item: {id: string}) => item.id === id).available).toBe(false)
  // Explicit test-operator restoration, not product auto-adoption or CAS bypass.
  writeFileSync(disk, original)
  await page.goto(`/notebooks/${id}`)
  await expect(page.locator('.conflict')).toContainText('print(99)')
  await page.getByRole('button', { name: 'Keep drafts for review', exact: true }).click()
  await page.getByRole('button', { name: 'Save notebook', exact: true }).click()
  await expect.poll(() => JSON.parse(readFileSync(disk, 'utf8')).cells[0].source).toBe('print(99)')
  await expect(page.getByRole('button', { name: 'Save notebook', exact: true })).toBeDisabled()
  await expect(page.locator('footer.status')).toContainText('Autosave paused')
})

test('lost physical creation acknowledgement retries the exact operation without a second registration', async ({ page, request }) => {
  let drop = true
  const operations: string[] = []
  let firstId = ''
  await page.route('**/api/workspace/notebooks', async route => {
    operations.push(route.request().postDataJSON().operation_id)
    const response = await route.fetch()
    if (drop) { drop = false; firstId = (await response.json()).notebook_id; await route.abort('failed') }
    else await route.fulfill({ response })
  })
  await page.goto('/browse?path=Existing')
  await page.getByRole('button', { name: 'New', exact: true }).first().click()
  await page.getByRole('menuitem', { name: 'Notebook · Python 3', exact: true }).click()
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Lost acknowledgement')
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('exact replay')
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: 'Retry exact operation', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${firstId}$`))
  expect(operations).toHaveLength(2); expect(operations[1]).toBe(operations[0])
  const canonical = await (await request.get(`/api/notebooks/${firstId}/conversation`)).json()
  expect((await (await request.get(`/api/notebooks/${firstId}/conversation`)).json()).id).toBe(canonical.id)
  const file = JSON.parse(readFileSync(join(process.env.EUROPA_B_WORKSPACE!, 'Existing/Lost acknowledgement.ipynb'), 'utf8'))
  expect(file.metadata.europa.notebook_id).toBe(firstId)
})

test('a real timed-out execution remains uncertain without navigation resubmission', async ({ page, request }) => {
  const { id, cell } = await seed(request, 'Uncertain execution', 'from threading import Event\nEvent().wait(120)')
  let submissions = 0
  await page.route(`**/api/notebooks/${id}/cells/${cell}/execute`, route => {
    submissions++
    return route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), timeout: 0.05 }) })
  })
  await page.goto(`/notebooks/${id}`)
  await page.locator('.cm-content').click()
  await page.getByRole('button', { name: 'Run selected cell', exact: true }).click()
  await expect(page.locator('footer.status')).toContainText('Uncertain receipt')
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  const history = await (await request.get(`/api/notebooks/${id}/executions`)).json()
  expect(history.items).toHaveLength(1); expect(history.items[0].state).toBe('uncertain')
  await page.goto(`/notebooks/${id}`)
  await expect(page.locator('footer.status')).toContainText('Uncertain receipt')
  expect(submissions).toBe(1)
})

test('removed physical notebooks stay unavailable and invalid routes fail safely', async ({ page, request }) => {
  const { id, path } = await seed(request, 'Removed document')
  await page.goto(`/notebooks/${id}`)
  await expect(page.locator('.cm-content')).toContainText('print(1)')
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect.poll(async () => (await (await request.get('/api/dashboard/recent')).json()).items.some((item: {id: string}) => item.id === id)).toBe(true)
  unlinkSync(join(process.env.EUROPA_B_WORKSPACE!, path))
  await page.getByRole('button', { name: 'Recent notebooks' }).click()
  await expect(page.getByRole('menuitem', { name: 'Unavailable notebook Unavailable', exact: true }).first()).toBeDisabled()
  expect((await (await request.get('/api/dashboard/recent')).json()).items.find((item: {id: string}) => item.id === id).available).toBe(false)
  await page.goto(`/notebooks/${id}`)
  await expect(page.getByRole('heading', { name: /Unable to open notebook|Notebook not found/ })).toBeVisible()
  expect((await request.get(`/api/notebooks/${id}`)).ok()).toBe(false)
  const invalid = await page.goto('/invalid-fixture-route')
  expect(invalid?.status()).toBe(404)
  expect((await request.get('/api/invalid-fixture-route')).status()).toBe(404)
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Files', exact: true })).toBeVisible()
})
