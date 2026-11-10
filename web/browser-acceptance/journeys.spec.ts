import { test, expect, type Page, type APIRequestContext } from '../browserFixtures'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { findSessionRow } from '../browserSessionNavigation'

const captureRoot = process.env.EUROPA_CAPTURE_ROOT || resolve('../docs/screenshots/phase3h/final')
async function capture(page: Page, name: string) {
  const dir = join(captureRoot, 'acceptance'); mkdirSync(dir, { recursive: true })
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: join(dir, name + '.png'), fullPage: true, animations: 'disabled' })
}
async function fileMenu(page: Page, item: string) {
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: item, exact: true }).click()
}
async function disableAutosave(page: Page) {
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View', exact: true }).click()
  const item = page.getByRole('menuitemcheckbox', { name: /Autosave/ })
  if (await item.getAttribute('aria-checked') === 'true') await item.click()
  else await page.keyboard.press('Escape')
}
for (const context of ['project', 'standalone', 'catalog'] as const) {
  test(`complete saved-state and verified kernel journey: ${context}`, async ({ page, request }, info) => {
    const title = `Journey ${context}`
    const directory = context === 'project' ? 'Journey Project' : 'Journey Standalone'
    let id: string
    if (context === 'catalog') {
      const response = await request.post('/api/notebooks', { data: { title } }); expect(response.status()).toBe(201)
      id = (await response.json()).metadata.europa.notebook_id
      await page.goto(`/notebooks/${id}`)
    } else {
      const response = await request.post(`/api/workspace/${context === 'project' ? 'projects' : 'folders'}`, { data: { path: directory, operation_id: crypto.randomUUID() } }); expect(response.status()).toBe(201)
      const created = await request.post('/api/workspace/notebooks', { data: { path: `${directory}/${title}.ipynb`, operation_id: crypto.randomUUID() } }); expect(created.status()).toBe(201)
      id = (await created.json()).notebook_id
      await page.goto('/')
      await page.getByRole('button', { name: directory + (context === 'project' ? ' Project' : ''), exact: true }).click()
      await page.getByRole('button', { name: title + '.ipynb', exact: true }).click()
    }
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    const canonical = await (await request.get(`/api/notebooks/${id}/conversation`)).json()
    await page.getByRole('button', { name: 'Insert cell', exact: true }).click()
    const editor = page.locator('.cell .cm-content')
    await editor.click(); await page.keyboard.insertText('retained_value = 42\nprint(retained_value)')
    await page.getByRole('button', { name: 'Run selected cell', exact: true }).click()
    await expect(page.locator('.cell .outputs')).toContainText('42')
    await expect(page.locator('footer.status')).toContainText('No pending receipt')
    await expect(page.locator('footer.status')).toContainText('Document saved')
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill(`Conversation retained for ${context}`)
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(page.locator('article.message')).toContainText(`Conversation retained for ${context}`)
    const saved = await (await request.get(`/api/notebooks/${id}`)).json()
    // Operational identity is asserted only in test infrastructure, never exposed by dashboard rows.
    const runtime = await (await request.get(`/api/runtime?notebook_id=${id}`)).json()
    const receipts = await (await request.get(`/api/notebooks/${id}/executions`)).json()
    const receipt = await (await request.get(`/api/notebooks/${id}/executions/${receipts.items[0].execution_id}`)).json()
    expect(receipt.state).toBe('applied')
    const privateReceipt = JSON.parse(readFileSync(join(process.env.EUROPA_ACCEPTANCE_ROOT!, 'notebooks', `${id}.${receipt.execution_id}.execution.json`), 'utf8'))
    const kernelId: string = privateReceipt.kernel_id
    const originalKernel = await (await request.get(`/api/kernel/info?notebook_id=${id}&kernel_id=${kernelId}`)).json()
    await capture(page, `${context}-editor`)
    await fileMenu(page, 'Return Home')
    await expect(page).toHaveURL(/\/$/)
    const row = await findSessionRow(page, title)
    await expect(row.getByRole('cell', { name: 'Idle', exact: true })).toBeVisible()
    const after = await (await request.get(`/api/runtime?notebook_id=${id}`)).json()
    expect(after.runtime_id).toBe(runtime.runtime_id); expect(after.generation).toBe(runtime.generation)
    await row.getByRole('button', { name: title, exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
    await expect(page.locator('.cell .outputs')).toContainText('42')
    await expect(page.locator('article.message')).toContainText(`Conversation retained for ${context}`)
    expect((await (await request.get(`/api/notebooks/${id}/conversation`)).json()).id).toBe(canonical.id)
    expect(await (await request.get(`/api/notebooks/${id}`)).json()).toEqual(saved)
    // The actual original execution kernel must still contain its variable.
    const kernelModel = await request.get(`/api/kernel/info?notebook_id=${id}&kernel_id=${kernelId}`)
    expect(kernelModel.status()).toBe(200)
    expect((await kernelModel.json()).id).toBe(originalKernel.id)
    await info.attach('runtime-navigation', { body: JSON.stringify({ sameRuntime: after.runtime_id === runtime.runtime_id, generation: after.generation, receipt: receipt.state }), contentType: 'application/json' })
    await fileMenu(page, 'Close Notebook')
    if (context === 'catalog') await expect(page).toHaveURL(/\/$/)
    else {
      await expect(page).toHaveURL(new RegExp(`/browse\\?path=${encodeURIComponent(directory)}$`))
      const disk = JSON.parse(readFileSync(join(process.env.EUROPA_B_WORKSPACE!, directory, title + '.ipynb'), 'utf8'))
      expect(disk.metadata.europa.notebook_id).toBe(id)
      expect(disk.cells[0].outputs[0].text).toBe('42\n')
    }
    await page.goBack(); await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await page.goForward()
  })
}

test('delayed pre-save GET cannot regress the next Markdown move revision', async ({ page, request }, info) => {
  let doc = await (await request.post('/api/notebooks', { data: { title: 'Revision ordering regression' } })).json()
  const id = doc.metadata.europa.notebook_id
  for (const cell_type of ['code', 'markdown']) doc = await (await request.post(`/api/notebooks/${id}/cells`, { data: { revision: doc.metadata.europa.revision, cell_type, source: cell_type === 'code' ? 'print(1)' : '# Saved markdown' } })).json()
  let count = 0, held!: () => void, release!: () => void
  const snapshotHeld = new Promise<void>(resolve => { held = resolve }), mayReturn = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/api/notebooks/${id}`, async route => {
    if (route.request().method() === 'GET' && ++count === 2) {
      const response = await route.fetch(); held(); await mayReturn; await route.fulfill({ response })
    } else await route.continue()
  })
  await page.goto(`/notebooks/${id}`)
  await page.locator('.cell .cm-content').first().waitFor()
  await snapshotHeld
  await disableAutosave(page)
  await page.locator('.cell .cm-content').first().click(); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.insertText('print(2)')
  const savedResponse = page.waitForResponse(response => response.url().includes(`/cells/${doc.cells[0].id}`) && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: 'Save notebook', exact: true }).click()
  const saved = await (await savedResponse).json()
  release()
  await expect(page.locator('.save-indicator')).toContainText(`Revision ${saved.metadata.europa.revision}`)
  const moved = page.waitForResponse(response => response.url().endsWith('/move'))
  await page.getByRole('button', { name: 'Move cell 2 up', exact: true }).click()
  const response = await moved
  await info.attach('CAS-evidence', { body: JSON.stringify({ savedRevision: saved.metadata.europa.revision, moveRequest: response.request().postDataJSON(), responseStatus: response.status() }), contentType: 'application/json' })
  expect(response.status()).toBe(200)
  expect(response.request().postDataJSON().revision).toBe(saved.metadata.europa.revision)
  await expect(page.locator('.cell').first()).toHaveAttribute('aria-label', 'markdown cell 1')
})

test('multiple real notebooks retain separate idle kernels across dashboard navigation', async ({ page, request }, info) => {
  const notebooks: { id: string; kernel: string; title: string }[] = []
  for (const title of ['Parallel notes one', 'Parallel notes two']) {
    const doc = await (await request.post('/api/notebooks', { data: { title } })).json()
    const id = doc.metadata.europa.notebook_id
    await request.post(`/api/notebooks/${id}/cells`, { data: { revision: 1, source: 'print(123)' } })
    await page.goto(`/notebooks/${id}`)
    await page.locator('.cm-content').click()
    await page.getByRole('button', { name: 'Run selected cell', exact: true }).click()
    await expect(page.locator('.outputs')).toContainText('123')
    await expect(page.locator('footer.status')).toContainText('No pending receipt')
    const receipt = (await (await request.get(`/api/notebooks/${id}/executions`)).json()).items[0]
    const privateReceipt = JSON.parse(readFileSync(join(process.env.EUROPA_ACCEPTANCE_ROOT!, 'notebooks', `${id}.${receipt.execution_id}.execution.json`), 'utf8'))
    notebooks.push({ id, kernel: privateReceipt.kernel_id, title })
    await page.getByRole('link', { name: 'Europa', exact: true }).click()
  }
  for (const notebook of notebooks) {
    const row = await findSessionRow(page, notebook.title)
    await expect(row.getByRole('cell', { name: 'Idle', exact: true })).toBeVisible()
    const result = await request.get(`/api/kernel/info?notebook_id=${notebook.id}&kernel_id=${notebook.kernel}`)
    expect(result.status()).toBe(200); expect((await result.json()).id).toBe(notebook.kernel)
  }
  await info.attach('multiple-kernel-identity', { body: JSON.stringify({ notebooks: 2, distinctKernels: notebooks[0].kernel !== notebooks[1].kernel, verifiedIdle: true }), contentType: 'application/json' })
})
