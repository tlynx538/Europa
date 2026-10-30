import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { resolve } from 'node:path'

const api = 'http://127.0.0.1:18000/api'
const screenshot = (name: string) => resolve(process.cwd(), '../docs/screenshots/phase3f-b', name)

async function post(request: APIRequestContext, path: string, body: object) {
  const response = await request.post(`${api}${path}`, { data: { operation_id: crypto.randomUUID(), ...body } })
  expect([200, 201]).toContain(response.status())
  return response.json()
}

async function fixture(request: APIRequestContext) {
  const notebookResponse = await request.post(`${api}/notebooks`, { data: { title: 'Phase 3F B notebook' } })
  expect(notebookResponse.status()).toBe(201)
  const notebook = await notebookResponse.json()
  const id: string = notebook.metadata.europa.notebook_id
  const cell = await request.post(`${api}/notebooks/${id}/cells`, { data: { revision: 1, cell_type: 'code', source: 'print(42)' } })
  expect(cell.status()).toBe(201)
  const project = await post(request, '/projects', { title: 'Phase 3F B project' })
  await post(request, `/projects/${project.id}/notebooks`, { revision: 1, notebook_id: id })
  await post(request, '/roadmaps', { project_id: project.id, objective: 'Validate UI', tasks: [{ id: 'review', title: 'Review notebook', acceptance_criteria: ['Inspect code'] }] })
  const conversation = await post(request, '/conversations', { project_id: project.id })
  await post(request, `/conversations/${conversation.id}/messages`, { content: 'Review the result' })
  return { id, project }
}

async function openNotebook(page: Page, id: string) {
  await page.goto(`/notebooks/${id}`)
  await expect(page.getByRole('heading', { name: 'Phase 3F B notebook' })).toBeVisible()
  await expect(page.locator('.cell')).toHaveCount(1)
  await notebookStatus(page)
}

async function notebookStatus(page: Page) {
  const status = page.locator('footer.status[role="status"]')
  await expect(status).toContainText('API: connected')
  await expect(status).toContainText(/Kernel: (?:connected|disconnected|unavailable) \(\d+\)/)
  await expect(status).toContainText('Document saved')
}

async function editorScreenshot(page: Page, name: string) {
  await notebookStatus(page)
  await page.screenshot({ path: screenshot(name) })
}

async function noHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth,
    offenders: [...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(e => `${e.tagName.toLowerCase()}.${e.className}`) }))
  expect(dimensions.document, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.viewport)
}

async function panels(page: Page, roadmap: boolean, discussion: boolean) {
  await expect(page.getByRole('button', { name: 'Roadmap', exact: true })).toHaveAttribute('aria-expanded', String(roadmap))
  await expect(page.getByRole('button', { name: 'Discussion', exact: true })).toHaveAttribute('aria-expanded', String(discussion))
  if (roadmap) await expect(page.locator('#roadmap')).toBeVisible()
  else await expect(page.locator('#roadmap')).toBeHidden()
  if (discussion) await expect(page.locator('#discussion')).toBeVisible()
  else await expect(page.locator('#discussion')).toBeHidden()
}

test('project browser, project detail and notebook library survive direct refresh and history', async ({ page, request }) => {
  const { id, project } = await fixture(request)
  await page.goto('/projects')
  await expect(page.getByRole('heading', { name: 'Projects', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Phase 3F B project' })).toBeVisible()
  await page.getByRole('searchbox', { name: 'Search projects' }).fill('Phase 3F B project')
  await page.screenshot({ path: screenshot('projects.png') })
  await page.getByRole('link', { name: 'Phase 3F B project' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}$`))
  await expect(page.getByRole('heading', { name: 'Phase 3F B project' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Phase 3F B notebook' })).toBeVisible()
  await expect(page.getByText(/^Validate UI · 0%/)).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Phase 3F B project' })).toBeVisible()
  await page.screenshot({ path: screenshot('project-detail.png'), fullPage: true })
  await page.getByRole('link', { name: 'Notebooks', exact: true }).click()
  await expect(page).toHaveURL(/\/notebooks$/)
  await expect(page.getByRole('link', { name: 'Phase 3F B notebook' })).toBeVisible()
  await page.getByRole('searchbox', { name: 'Search notebooks' }).fill('Phase 3F B notebook')
  await page.screenshot({ path: screenshot('notebook-library.png') })
  await page.getByRole('link', { name: 'Phase 3F B notebook' }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
  await expect(page.locator('.cell')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.cell')).toHaveCount(1)
  await page.goBack()
  await expect(page).toHaveURL(/\/notebooks$/)
  await expect(page.getByRole('link', { name: 'Phase 3F B notebook' })).toBeVisible()
  await page.goForward()
  await expect(page.locator('.cell')).toHaveCount(1)
})

test('editor panels, roadmap and discussion render in both themes; menus and keyboard work', async ({ page, request }) => {
  const { id, project } = await fixture(request)
  await page.goto('/notebooks')
  await page.evaluate(() => localStorage.setItem('europa.theme', 'light'))
  await openNotebook(page, id)
  await panels(page, false, false)
  await editorScreenshot(page, 'editor-no-panels-light.png')
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await panels(page, true, false)
  await page.getByLabel('Project', { exact: true }).selectOption(project.id)
  await expect(page.getByText('Validate UI', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await expect(page.getByText('Review the result', { exact: true })).toBeVisible()
  await panels(page, true, true)
  await editorScreenshot(page, 'editor-both-light.png')
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await panels(page, true, false)
  await editorScreenshot(page, 'editor-roadmap-light.png')
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await panels(page, false, false)
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await panels(page, false, true)
  await editorScreenshot(page, 'editor-discussion-light.png')

  // Menus remain keyboard-operable; disabled actions must not silently mutate the notebook.
  await page.locator('.appbar-more').filter({ has: page.locator('summary', { hasText: 'File' }) }).locator('summary').click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
  await page.keyboard.press('Escape')
  const editor = page.locator('.cm-content').first()
  await editor.click()
  await expect(page.getByText('Mode: edit')).toBeVisible()
  await editor.press('Escape')
  await expect(page.getByText('Mode: command')).toBeVisible()
  await editor.click()
  await editor.press('Shift+Enter')
  await expect(page.locator('.cell .outputs')).toContainText('42', { timeout: 45000 })

  await page.locator('.appbar-more').filter({ has: page.locator('summary', { hasText: 'View' }) }).locator('summary').click()
  await page.getByLabel('Theme').selectOption('dark')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  for (const label of ['File', 'View']) {
    const menu = page.locator('.appbar-more').filter({ has: page.locator('summary', { hasText: label }) })
    if (await menu.getAttribute('open') !== null) await menu.locator('summary').click()
  }
  await expect(page.locator('.appbar-more[open]')).toHaveCount(0)
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await panels(page, true, true)
  await editorScreenshot(page, 'editor-both-dark.png')
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await panels(page, true, false)
  await editorScreenshot(page, 'editor-roadmap-dark.png')
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await panels(page, false, false)
  await editorScreenshot(page, 'editor-no-panels-dark.png')
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await panels(page, false, true)
  await editorScreenshot(page, 'editor-discussion-dark.png')
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByText('Validate UI', { exact: true })).toBeVisible()
  await expect(page.getByText('Review the result', { exact: true })).toBeVisible()
  await page.locator('.cell .toolbar strong').first().click()
  await page.keyboard.press('b')
  await expect(page.locator('.cell')).toHaveCount(2)
})

test('mobile drawers and zoom-sized viewport do not overflow', async ({ page, request }) => {
  const { id, project } = await fixture(request)
  await page.addInitScript(() => localStorage.setItem('europa.theme', 'light'))
  await page.setViewportSize({ width: 390, height: 844 })
  await openNotebook(page, id)
  await panels(page, false, false)
  await noHorizontalOverflow(page)
  await editorScreenshot(page, 'mobile-editor.png')
  await page.getByRole('button', { name: 'Roadmap', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Project and roadmap' })).toBeVisible()
  await page.getByLabel('Project', { exact: true }).selectOption(project.id)
  await expect(page.getByText('Validate UI', { exact: true })).toBeVisible()
  await noHorizontalOverflow(page)
  await editorScreenshot(page, 'mobile-roadmap.png')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Roadmap', exact: true })).toBeFocused()
  await page.getByRole('button', { name: 'Discussion', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Discussion', exact: true })).toBeVisible()
  await expect(page.getByText('Review the result', { exact: true })).toBeVisible()
  await noHorizontalOverflow(page)
  await editorScreenshot(page, 'mobile-discussion.png')
  await page.keyboard.press('Escape')
  // A narrow CSS viewport approximates browser zoom at desktop pixel dimensions.
  await page.setViewportSize({ width: 320, height: 720 })
  await noHorizontalOverflow(page)
})
