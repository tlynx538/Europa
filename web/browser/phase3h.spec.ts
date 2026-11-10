import { expect, test, type APIRequestContext, type Page } from '../browserFixtures'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const api = `http://127.0.0.1:${process.env.EUROPA_TEST_PORT || '18000'}/api`
const captures = resolve(process.cwd(), process.env.EUROPA_CAPTURE_ROOT ? `${process.env.EUROPA_CAPTURE_ROOT}/catalog` : '../docs/screenshots/phase3h/h1')

async function fixture(request: APIRequestContext) {
  const notebookResponse = await request.post(`${api}/notebooks`, { data: { title: 'H1 fixture notes' } })
  expect(notebookResponse.status(), await notebookResponse.text()).toBe(201)
  const notebook = await notebookResponse.json()
  const id: string = notebook.metadata.europa.notebook_id
  const cellResponse = await request.post(`${api}/notebooks/${id}/cells`, { data: { revision: 1, cell_type: 'code', source: 'print(1)' } })
  expect(cellResponse.status(), await cellResponse.text()).toBe(201)
  const projectResponse = await request.post(`${api}/projects`, { data: { operation_id: crypto.randomUUID(), title: 'H1 fixture folder' } })
  expect(projectResponse.status(), await projectResponse.text()).toBe(201)
  const project = await projectResponse.json()
  const association = await request.post(`${api}/projects/${project.id}/notebooks`, { data: { operation_id: crypto.randomUUID(), revision: project.revision, notebook_id: id } })
  expect(association.ok(), await association.text()).toBe(true)
  return { id, projectId: project.id }
}

async function capture(page: Page, name: string) {
  mkdirSync(captures, { recursive: true })
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: resolve(captures, name), fullPage: true, animations: 'disabled' })
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}

test('fixture workspace opens project and notebook without starting runtime, preserves recents and history', async ({ page, request }) => {
  const { id, projectId } = await fixture(request)
  const runtimePosts: string[] = []
  page.on('request', req => {
    if (req.method() === 'POST' && /\/(?:kernels|sessions|execute)(?:\/|$)/.test(new URL(req.url()).pathname)) runtimePosts.push(req.url())
  })
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Files', exact: true })).toBeVisible()
  await capture(page, 'home-desktop.png')
  expect(runtimePosts).toEqual([])
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Existing catalog projects', exact: true }).click()
  await page.getByRole('link', { name: 'H1 fixture folder', exact: true }).last().click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`))
  await expect(page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' })).toBeVisible()
  await expect(page.locator('.project-details')).not.toHaveAttribute('open', '')
  await capture(page, 'folder-desktop.png')
  await page.reload()
  await expect(page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' })).toBeVisible()
  await page.getByRole('link', { name: 'H1 fixture notes' }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
  await expect(page.locator('footer.status')).toContainText('Document saved')
  await capture(page, 'editor-desktop.png')
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await capture(page, 'file-menu-desktop.png')
  await page.getByRole('menuitem', { name: 'Close notebook' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`))
  const reopened = page.waitForResponse(response => response.url().endsWith(`/workspace/notebooks/${id}/location`) && response.request().method() === 'GET')
  await page.goBack()
  await reopened
  await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
  await expect(page.locator('footer.status')).toContainText('Document saved')
  await page.goForward()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`))
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.getByRole('button', { name: 'Recent notebooks' }).click()
  await expect(page.getByRole('menuitem', { name: /H1 fixture notes/ }).first()).toBeVisible()
  await expect(page.getByRole('menuitem', { name: /H1 fixture notes/ }).first()).toContainText('Catalog / H1 fixture folder')
  await capture(page, 'home-with-recent.png')
  await page.keyboard.press('Escape')
  expect(runtimePosts).toEqual([])

  await page.setViewportSize({ width: 390, height: 844 })
  await noOverflow(page)
  await capture(page, 'home-mobile.png')
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Existing catalog projects', exact: true }).click()
  await page.getByRole('link', { name: 'H1 fixture folder', exact: true }).last().click()
  await expect(page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' })).toBeVisible()
  await noOverflow(page)
  await capture(page, 'folder-mobile.png')
})

test('returning Home before history POST completes updates server recents when it finishes', async ({ page, request }) => {
  const { id, projectId } = await fixture(request)
  let releaseVisit!: () => void
  const visitCanComplete = new Promise<void>(resolve => { releaseVisit = resolve })
  let visitIntercepted!: () => void
  const visitStarted = new Promise<void>(resolve => { visitIntercepted = resolve })
  let postCount = 0
  await page.route(`**/api/notebooks/${id}/opened`, async route => {
    postCount++
    visitIntercepted()
    await visitCanComplete
    await route.continue()
  })
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' }).click()
  await expect(page.locator('footer.status')).toContainText('Document saved')
  await visitStarted
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.getByRole('button', { name: 'Recent notebooks' }).click()
  const recentItems = async () => (await (await request.get(`${api}/dashboard/recent`)).json()).items as { id: string }[]
  expect((await recentItems()).some(item => item.id === id)).toBe(false)
  releaseVisit()
  await expect.poll(async () => (await recentItems())[0]?.id).toBe(id)
  await expect(page.getByRole('menuitem', { name: /H1 fixture notes/ }).first()).toBeVisible()
  expect(postCount).toBe(1)
})

test('declined leave keeps unsaved notebook draft and URL, then permits navigation', async ({ page, request }) => {
  const { id, projectId } = await fixture(request)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' }).click()
  const editor = page.locator('.cell .cm-content')
  await expect(editor).toBeVisible()
  await editor.click()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.type('print(2)')
  await expect(page.locator('.save-indicator')).toContainText('Unsaved draft')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
  await expect(editor).toContainText('print(2)')
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await page.getByRole('menuitem', { name: 'Close notebook' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`))
  await page.getByRole('region', { name: 'Notebooks' }).getByRole('link', { name: 'H1 fixture notes' }).click()
  await expect(page.locator('.cell .cm-content')).toContainText('print(2)')
})
