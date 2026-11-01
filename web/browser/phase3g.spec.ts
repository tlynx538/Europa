import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { mkdirSync, readFileSync, realpathSync, writeFileSync, rmSync } from 'node:fs'
import { resolve, join } from 'node:path'
const fixture = JSON.parse(readFileSync(new URL('./fixtures/phase3g.json', import.meta.url), 'utf8')) as Record<string, string>

const api = `http://127.0.0.1:${process.env.EUROPA_TEST_PORT || '18000'}/api`
const output = resolve(process.cwd(), '../docs/screenshots/phase3g', process.env.EUROPA_PHASE3G_CAPTURE || 'after')
const toolbar = (page: Page) => page.getByRole('toolbar', { name: 'Notebook commands' })
type Receipt = { execution_id: string; cell_id: string; state: string; result?: { status: string } }

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(`${api}${path}`, { data })
  expect(response.status(), await response.text()).toBe(201)
  return response.json()
}
async function projectPost(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(`${api}${path}`, { data: { operation_id: crypto.randomUUID(), ...data } })
  expect([200, 201], await response.text()).toContain(response.status())
  return response.json()
}
async function add(request: APIRequestContext, id: string, revision: number, cell_type: 'code' | 'markdown', source: string) {
  return post(request, `/notebooks/${id}/cells`, { revision, cell_type, source })
}
async function toggleRoadmap(page: Page) {
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View' }).click()
  await page.getByRole('menuitemcheckbox', { name: 'Roadmap panel' }).click()
}
async function ready(page: Page) {
  await expect(page.locator('footer.status')).toContainText('API: connected')
  await expect(page.locator('footer.status')).toContainText('Document saved')
  await expect(page.locator('footer.status')).toContainText('No pending receipt')
}
async function settled(page: Page, request: APIRequestContext, id: string, cellId: string, executionId: string, status: 'ok' | 'error') {
  await expect.poll(async () => {
    const response = await request.get(`${api}/notebooks/${id}/executions/${executionId}`)
    expect(response.ok()).toBe(true)
    const receipt = await response.json() as Receipt
    expect(receipt.cell_id).toBe(cellId)
    return `${receipt.state}:${receipt.result?.status}`
  }, { timeout: 45000 }).toBe(`applied:${status}`)
  await expect(page.locator(`#cell-${cellId}`)).toContainText(`Execution: applied (${status})`, { timeout: 45000 })
  await expect(page.locator(`#cell-${cellId}`).getByLabel(/Execution history for cell/)).toBeVisible()
  await ready(page) // An applied receipt alone does not imply the UI operation lock is released.
  await page.locator(`#cell-${cellId}`).focus()
  await expect(toolbar(page).getByRole('button', { name: 'Run selected cell' })).toBeEnabled()
}
async function capture(page: Page, info: TestInfo, name: string, target?: string, fullPage = false) {
  if (target) await page.locator(target).scrollIntoViewIfNeeded()
  await page.evaluate(() => document.fonts.ready)
  const frames = page.locator('iframe[title="Isolated notebook output"]')
  for (let i = 0; i < await frames.count(); i++) await expect(frames.nth(i).contentFrame().locator('body')).not.toBeEmpty()
  const dimensions = await page.evaluate(target => ({
    viewport: { width: innerWidth, height: innerHeight }, deviceScaleFactor: devicePixelRatio,
    zoom: getComputedStyle(document.documentElement).zoom,
    document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
    notebook: document.querySelector('.notebook-surface')?.getBoundingClientRect().toJSON() ?? null,
    target: target ? document.querySelector(target)?.getBoundingClientRect().toJSON() ?? null : null,
    cells: [...document.querySelectorAll('.cell')].map(e => e.getBoundingClientRect().toJSON()),
    panels: [...document.querySelectorAll('.side-panel')].map(e => e.getBoundingClientRect().toJSON()),
  }), target)
  expect(dimensions.document.width, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.viewport.width)
  expect(dimensions.deviceScaleFactor).toBe(1)
  expect(dimensions.zoom).toBe('1')
  expect(dimensions.viewport).toEqual({ width: page.viewportSize()!.width, height: page.viewportSize()!.height })
  if (target) {
    expect(dimensions.target, `Missing screenshot target ${target}`).not.toBeNull()
    expect(dimensions.target!.bottom).toBeGreaterThan(0)
    expect(dimensions.target!.top).toBeLessThan(dimensions.viewport.height)
  }
  await info.attach(`${name}-layout`, { body: JSON.stringify(dimensions, null, 2), contentType: 'application/json' })
  mkdirSync(output, { recursive: true })
  await page.screenshot({ path: join(output, `${name}.png`), fullPage, animations: 'disabled' })
}
async function run(page: Page, request: APIRequestContext, notebook: string, cell: string, status: 'ok' | 'error' = 'ok') {
  await page.locator(`#cell-${cell} .cm-content`).click()
  const selection = await page.evaluate(() => ({
    selected: [...document.querySelectorAll('.cell.selected')].map(element => element.id),
    active: document.activeElement?.outerHTML.slice(0, 300),
    frameActive: document.activeElement instanceof HTMLIFrameElement,
    scrollY, toolbar: document.querySelector('.editor-tools')?.getBoundingClientRect().toJSON(),
  }))
  expect(selection.selected, `Expected #cell-${cell} after editor click; focus: ${JSON.stringify(selection)}`).toEqual([`cell-${cell}`])
  expect(selection.frameActive, `Editor click focused output iframe: ${JSON.stringify(selection)}`).toBe(false)
  const requests: string[] = []
  const record = (sent: import('@playwright/test').Request) => { if (sent.method() === 'POST' && sent.url().includes('/execute')) requests.push(sent.url()) }
  page.on('request', record)
  const execution = page.waitForResponse(response => response.url().endsWith(`/cells/${cell}/execute`) && response.request().method() === 'POST', { timeout: 20000 })
  await toolbar(page).getByRole('button', { name: 'Run selected cell' }).click()
  let response
  try { response = await execution }
  catch (error) {
    const after = await page.evaluate(() => ({ selected: [...document.querySelectorAll('.cell.selected')].map(element => element.id), active: document.activeElement?.outerHTML.slice(0, 300), scrollY, toolbar: document.querySelector('.editor-tools')?.getBoundingClientRect().toJSON() }))
    throw new Error(`No execution response for ${cell}; requests: ${JSON.stringify(requests)}; before: ${JSON.stringify(selection)}; after: ${JSON.stringify(after)}`, { cause: error })
  } finally { page.off('request', record) }
  expect(response.status()).toBe(202)
  await settled(page, request, notebook, cell, (await response.json()).execution_id, status)
}

test('real execution visual baseline: notebook, MIME outputs, panels, menus and mobile', async ({ page, request }, info) => {
  test.setTimeout(240000)
  await page.addInitScript(() => localStorage.setItem('europa.theme', 'light'))
  const one = await post(request, '/notebooks', { title: 'Phase 3G single code cell' })
  const oneId: string = one.metadata.europa.notebook_id
  await add(request, oneId, 1, 'code', fixture.one_cell)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/notebooks/${oneId}`)
  await expect(page.locator('.cell')).toHaveCount(1)
  await ready(page)
  await capture(page, info, 'one-cell-1440x900')
  await page.setViewportSize({ width: 1280, height: 800 })
  await capture(page, info, 'one-cell-1280x800')
  await page.setViewportSize({ width: 1440, height: 900 })
  const notebook = await post(request, '/notebooks', { title: 'Phase 3G weekly observations' })
  const id: string = notebook.metadata.europa.notebook_id
  const sources: [string, 'markdown' | 'code'][] = [[fixture.markdown, 'markdown'], [fixture.computation, 'code'], [fixture.table, 'code'], [fixture.plot, 'code'], [fixture.error, 'code']]
  let document = notebook
  const ids: string[] = []
  for (const [source, kind] of sources) {
    document = await add(request, id, document.metadata.europa.revision, kind, source)
    ids.push(document.cells.at(-1).id)
  }
  const project = await projectPost(request, '/projects', { title: 'Phase 3G visual project' })
  await projectPost(request, `/projects/${project.id}/notebooks`, { revision: 1, notebook_id: id })
  await projectPost(request, '/roadmaps', { project_id: project.id, objective: 'Review weekly observations', tasks: [{ id: 'review', title: 'Review results', acceptance_criteria: ['Inspect the notebook'] }] })
  const canonicalResponse = await request.get(`${api}/notebooks/${id}/conversation`)
  expect(canonicalResponse.status()).toBe(200)
  const conversation = await canonicalResponse.json()
  await projectPost(request, `/conversations/${conversation.id}/messages`, { content: 'Review the weekly chart and table.' })
  await page.goto(`/notebooks/${id}`)
  await expect(page.locator('.cell')).toHaveCount(5)
  await ready(page)
  await expect(page.locator(`#cell-${ids[0]} .markdown-preview h1`)).toHaveText('Weekly observations')
  await capture(page, info, 'markdown-code-1440x900', `#cell-${ids[0]}`)
  await run(page, request, id, ids[1])
  await expect(page.locator(`#cell-${ids[1]} .outputs`)).toContainText('Average count: 19.4')
  await run(page, request, id, ids[2])
  await expect(page.locator(`#cell-${ids[2]} iframe`).contentFrame().getByRole('table')).toContainText('Results')
  await capture(page, info, 'table-1440x900', `#cell-${ids[2]} .cell-output`)
  await run(page, request, id, ids[3])
  // Output sanitization intentionally removes aria attributes; assert the actual rendered SVG geometry.
  await expect(page.locator(`#cell-${ids[3]} iframe`).contentFrame().locator('svg circle')).toHaveCount(5)
  await expect(page.locator(`#cell-${ids[3]} iframe`).contentFrame().locator('svg text')).toContainText(['Weekly observations', 'Week', 'Count', '1', '2', '3', '4', '5'])
  await capture(page, info, 'plot-1440x900', `#cell-${ids[3]} .cell-output`)
  await run(page, request, id, ids[4], 'error')
  await expect(page.locator(`#cell-${ids[4]} .outputs`)).toContainText('ValueError: Example invalid measurement')
  await capture(page, info, 'traceback-1440x900', `#cell-${ids[4]} .cell-output`)
  const focusBeforeClick = process.env.EUROPA_PHASE3G_TRACE === '1' ? await page.evaluate(cell => {
    const events: object[] = []
    const describe = (node: EventTarget | null) => {
      if (!(node instanceof Element)) return String(node)
      return { tag: node.tagName, id: node.id, className: typeof node.className === 'string' ? node.className.slice(0, 100) : '', cell: node.closest('.cell')?.id ?? null }
    }
    const rect = (selector: string) => document.querySelector(selector)?.getBoundingClientRect().toJSON() ?? null
    const snapshot = () => ({
      scrollY, active: describe(document.activeElement), selected: [...document.querySelectorAll('.cell.selected')].map(e => e.id),
      editor: rect(`#cell-${cell} .cm-content`), cell: rect(`#cell-${cell}`), appbar: rect('.appbar'), toolbar: rect('.editor-tools'),
    })
    const record = (entry: object) => { events.push({ time: performance.now(), scrollY, active: describe(document.activeElement), ...entry }); if (events.length > 160) events.shift() }
    for (const name of ['pointerdown', 'mousedown', 'focusin', 'focusout', 'mouseup', 'click'] as const) {
      document.addEventListener(name, event => {
        const pointer = event as MouseEvent
        record({ kind: name, target: describe(event.target), x: pointer.clientX ?? null, y: pointer.clientY ?? null,
          hit: typeof pointer.clientX === 'number' ? describe(document.elementFromPoint(pointer.clientX, pointer.clientY)) : null })
      }, true)
    }
    window.addEventListener('scroll', () => record({ kind: 'native-scroll', geometry: snapshot() }), true)
    const originalIntoView = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (...args) {
      record({ kind: 'scrollIntoView', target: describe(this), args, stack: new Error().stack })
      return originalIntoView.apply(this, args)
    }
    for (const name of ['scrollBy', 'scrollTo'] as const) {
      const original = window[name]
      window[name] = function (...args: [ScrollToOptions] | [number, number]) {
        record({ kind: name, args, stack: new Error().stack })
        return (original as (...values: [ScrollToOptions] | [number, number]) => void).apply(window, args)
      }
    }
    const originalFocus = HTMLElement.prototype.focus
    HTMLElement.prototype.focus = function (...args) {
      record({ kind: 'focus', target: describe(this), args, stack: new Error().stack })
      return originalFocus.apply(this, args)
    }
    Object.assign(window, { __phase3gTrace: { events, snapshot } })
    return snapshot()
  }, ids[1]) : null
  let focusAfterClick: object | undefined
  try {
    await page.locator(`#cell-${ids[1]} .cm-content`).evaluate(element => element.scrollIntoView({ block: 'center' }))
    await page.locator(`#cell-${ids[1]} .cm-content`).click()
    if (focusBeforeClick) focusAfterClick = await page.evaluate(() => (window as Window & { __phase3gTrace: { snapshot: () => object } }).__phase3gTrace.snapshot())
    await expect(page.getByText('Mode: edit'), JSON.stringify(focusAfterClick)).toBeVisible({ timeout: 3000 })
  } catch (error) {
    if (!focusBeforeClick) throw error
    const trace = await page.evaluate(() => {
      const { events, snapshot } = (window as Window & { __phase3gTrace: { events: object[]; snapshot: () => object } }).__phase3gTrace
      return { events, final: snapshot() }
    })
    const body = JSON.stringify({ before: focusBeforeClick, after: focusAfterClick, ...trace }, null, 2)
    mkdirSync('/private/tmp/europa-phase3g', { recursive: true })
    writeFileSync('/private/tmp/europa-phase3g/focus-events.json', body)
    await info.attach('focus-events', { body, contentType: 'application/json' })
    throw error
  }
  await capture(page, info, 'focused-edit-1440x900', `#cell-${ids[1]}`)
  await page.keyboard.press('Escape')
  await expect(page.getByText('Mode: command')).toBeVisible()
  await expect(page.locator(`#cell-${ids[1]}`)).toHaveClass(/selected/)
  await capture(page, info, 'selected-command-1440x900', `#cell-${ids[1]}`)
  await capture(page, info, 'overview-1440x900', `#cell-${ids[0]}`)
  await capture(page, info, 'overview-full-page-1440x900', undefined, true)
  await page.setViewportSize({ width: 1280, height: 800 })
  await capture(page, info, 'overview-1280x800', `#cell-${ids[0]}`)
  await capture(page, info, 'overview-full-page-1280x800', undefined, true)
  await page.setViewportSize({ width: 1440, height: 900 })
  await toggleRoadmap(page)
  await expect(page.getByText('Review weekly observations', { exact: true })).toBeVisible()
  await capture(page, info, 'roadmap-1440x900')
  await page.getByLabel('Message', { exact: true }).focus()
  await expect(page.getByText('Review the weekly chart and table.')).toBeVisible()
  await capture(page, info, 'mixed-stream-roadmap-1440x900')
  await toggleRoadmap(page)
  await capture(page, info, 'mixed-stream-1440x900')
  const desktopChrome = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, header: document.querySelector('.appbar')!.getBoundingClientRect().toJSON(), footer: document.querySelector('footer.status')!.getBoundingClientRect().toJSON(), title: document.querySelector('.notebook-chrome-title')!.getBoundingClientRect().toJSON(), heading: document.querySelector('.notebook-chrome-title h1')!.getBoundingClientRect().toJSON(), links: document.querySelectorAll('.notebook-chrome-title a').length }))
  expect(desktopChrome.links).toBe(0)
  expect(desktopChrome.heading.left).toBe(desktopChrome.title.left)
  expect(desktopChrome.header.left).toBe(0)
  expect(desktopChrome.header.right).toBe(desktopChrome.width)
  expect(desktopChrome.footer.left).toBe(0)
  expect(desktopChrome.footer.right).toBe(desktopChrome.width)
  expect(desktopChrome.footer.bottom).toBe(desktopChrome.height)
  for (const name of ['File', 'Edit', 'View', 'Run', 'Kernel', 'Help']) {
    const menu = page.getByRole('menubar').getByRole('menuitem', { name })
    await menu.click()
    await expect(page.getByRole('menu', { name })).toBeVisible()
    if (name === 'File') {
      await expect(page.getByRole('menuitem', { name: 'Open project…' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Open notebook…' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Projects', exact: true })).toHaveCount(0)
      await expect(page.getByRole('menuitem', { name: 'Notebooks', exact: true })).toHaveCount(0)
    }
    await capture(page, info, `menu-${name.toLowerCase()}-1440x900`)
    await page.keyboard.press('Escape')
  }
  await page.setViewportSize({ width: 390, height: 844 })
  // ResizeObserver updates the shell inset after the wrapped mobile footer is measured.
  await expect.poll(() => page.evaluate(() => {
    const editor = document.querySelector('.notebook-editor')!.getBoundingClientRect()
    const footer = document.querySelector('footer.status')!.getBoundingClientRect()
    return Math.round(editor.bottom - footer.top)
  })).toBeLessThanOrEqual(0)
  const chrome = await page.evaluate(() => ({ viewport: innerWidth, height: innerHeight, header: document.querySelector('.appbar')!.getBoundingClientRect().toJSON(), footer: document.querySelector('footer.status')!.getBoundingClientRect().toJSON(), editor: document.querySelector('.notebook-editor')!.getBoundingClientRect().toJSON() }))
  expect(chrome.header.left).toBe(0)
  expect(chrome.header.right).toBe(chrome.viewport)
  expect(chrome.footer.left).toBe(0)
  expect(chrome.footer.right).toBe(chrome.viewport)
  expect(chrome.footer.bottom).toBe(chrome.height)
  expect(chrome.editor.bottom).toBeLessThanOrEqual(chrome.footer.top)
  await capture(page, info, 'notebook-390x844')
  await toggleRoadmap(page)
  await expect(page.getByRole('dialog', { name: 'Project and roadmap' })).toBeVisible()
  await capture(page, info, 'roadmap-drawer-390x844')
  await page.keyboard.press('Escape')
  await page.getByLabel('Message', { exact: true }).focus()
  await expect(page.getByLabel('Message', { exact: true })).toBeFocused()
  await capture(page, info, 'composer-390x844')
})

test('View roadmap uses one modal on mobile and composer stays above the footer', async ({ page, request }) => {
  const created = await post(request, '/notebooks', { title: 'Panel and overlay geometry' })
  for (const width of [1440, 1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : width === 1280 ? 800 : 900 })
    await page.goto(`/notebooks/${created.metadata.europa.notebook_id}`)
    await expect(page.locator('.message-composer')).toBeVisible()
    await expect(page.locator('.appbar-controls button')).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => {
      const composer = document.querySelector('.composer-shell')!.getBoundingClientRect()
      const footer = document.querySelector('.global-status')!.getBoundingClientRect()
      return Math.round(composer.bottom - footer.top)
    })).toBe(0)
    await toggleRoadmap(page)
    await expect(page.locator('#roadmap')).toBeVisible()
    if (width === 390) {
      expect(await page.locator('#roadmap').evaluate(node => node.matches(':modal'))).toBe(true)
      await page.keyboard.press('Escape')
      await expect(page.locator('#roadmap')).toBeHidden()
      await expect(page.getByRole('menubar').getByRole('menuitem', { name: 'View' })).toBeFocused()
    } else await toggleRoadmap(page)
  }
})

test('File project settings is one modal with bounded mobile layout and guarded project management', async ({ page, request }, info) => {
  const created = await post(request, '/notebooks', { title: 'Settings overlay notebook' })
  const id = created.metadata.europa.notebook_id as string
  await add(request, id, 1, 'code', 'print(1)')
  const project = await projectPost(request, '/projects', { title: 'Settings overlay project' })
  await projectPost(request, `/projects/${project.id}/notebooks`, { revision: 1, notebook_id: id })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/notebooks/${id}`)
  await ready(page)
  const file = page.getByRole('menubar').getByRole('menuitem', { name: 'File' })
  const dialog = page.getByRole('dialog', { name: 'Project settings' })
  await file.click()
  await page.getByRole('menuitem', { name: 'Project settings…' }).click()
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('section strong').first()).toHaveText('Settings overlay project')
  await expect(dialog.getByText('1 linked notebook')).toBeVisible()
  expect(await page.evaluate(() => ({ modal: document.querySelector('.project-settings-dialog')?.matches(':modal'), backdrop: getComputedStyle(document.querySelector('.project-settings-dialog')!, '::backdrop').backgroundImage, blur: getComputedStyle(document.querySelector('.project-settings-dialog')!, '::backdrop').backdropFilter }))).toMatchObject({ modal: true, blur: 'blur(12px)' })
  const backdrop = await page.evaluate(() => getComputedStyle(document.querySelector('.project-settings-dialog')!, '::backdrop').backgroundImage)
  expect(backdrop).toContain('radial-gradient')
  await expect.poll(() => page.evaluate(() => document.activeElement?.closest('.project-settings-dialog') !== null)).toBe(true)
  await dialog.getByRole('button', { name: 'Refresh project history' }).focus()
  await page.keyboard.press('Tab')
  await expect.poll(() => page.evaluate(() => document.activeElement?.closest('.project-settings-dialog') !== null)).toBe(true)
  const desktopBounds = await dialog.boundingBox()
  expect(desktopBounds).not.toBeNull()
  await dialog.click({ position: { x: 8, y: 8 } })
  await expect(dialog).toBeVisible()
  await page.mouse.click(desktopBounds!.x - 12, desktopBounds!.y + 24)
  await expect(dialog).toBeHidden()
  await expect(file).toBeFocused()
  await file.click()
  await page.getByRole('menuitem', { name: 'Project settings…' }).click()
  await expect(dialog).toBeVisible()
  const desktopGeometry = await page.evaluate(() => {
    const rect = (selector: string) => document.querySelector(selector)?.getBoundingClientRect().toJSON()
    return { title: rect('.notebook-chrome-title'), menu: rect('.notebook-menubar'), file: rect('.menu-trigger'), toolbar: rect('.editor-tools'), mode: rect('.editor-mode'), header: rect('.appbar') }
  })
  await capture(page, info, 'settings-1440x900', '.project-settings-dialog')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(file).toBeFocused()
  await file.click()
  await page.getByRole('menuitem', { name: 'Project settings…' }).click()
  await dialog.getByRole('button', { name: 'Close Project settings' }).click()
  await expect(file).toBeFocused()
  await toggleRoadmap(page)
  const emptySettings = page.locator('#roadmap').getByRole('button', { name: 'Open Project settings' })
  await emptySettings.click()
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(emptySettings).toBeFocused()
  await toggleRoadmap(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await file.click()
  await page.getByRole('menuitem', { name: 'Project settings…' }).click()
  const bounds = await dialog.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
  expect(bounds!.y).toBeGreaterThanOrEqual(0)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844)
  await capture(page, info, 'settings-390x844', '.project-settings-dialog')
  const geometry = await page.evaluate(() => {
    const rect = (selector: string) => document.querySelector(selector)?.getBoundingClientRect().toJSON()
    return { title: rect('.notebook-chrome-title'), menu: rect('.notebook-menubar'), file: rect('.menu-trigger'), toolbar: rect('.editor-tools'), mode: rect('.editor-mode'), header: rect('.appbar') }
  })
  writeFileSync('/private/tmp/europa-phase3g/settings-geometry.json', JSON.stringify({ desktop: desktopGeometry, mobile: geometry }, null, 2))
  await dialog.getByRole('button', { name: 'Close Project settings' }).click()
  await expect(file).toBeFocused()
  await toggleRoadmap(page)
  await page.locator('#roadmap').getByRole('button', { name: 'Open Project settings' }).click()
  await expect(dialog).toBeVisible()
  await expect(page.locator('#roadmap')).toBeHidden()
  expect(await page.evaluate(() => document.querySelectorAll('dialog:modal').length)).toBe(1)
  await dialog.getByRole('button', { name: 'Close Project settings' }).click()
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View' }).click()
  const autosave = page.getByRole('menuitemcheckbox', { name: 'Autosave' })
  if (await autosave.getAttribute('aria-checked') === 'true') await autosave.click()
  await page.locator('.cell .cm-content').click()
  await page.keyboard.insertText(' # keep local draft')
  await file.click()
  await page.getByRole('menuitem', { name: 'Project settings…' }).click()
  page.once('dialog', prompt => prompt.dismiss())
  await dialog.getByRole('button', { name: /Manage project/ }).click()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('status')).toContainText('Could not leave this notebook')
  await expect(page.locator('.cm-content')).toContainText('keep local draft')
  page.once('dialog', prompt => prompt.accept())
  await dialog.getByRole('button', { name: /Manage project/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}$`))
})

test('project, library and empty/populated persisted owner activity baseline', async ({ page, request }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const project = await projectPost(request, '/projects', { title: 'Phase 3G activity project' })
  const notebook = await post(request, '/notebooks', { title: 'Phase 3G library notebook' })
  await projectPost(request, `/projects/${project.id}/notebooks`, { revision: 1, notebook_id: notebook.metadata.europa.notebook_id })
  await page.goto(`/notebooks/${notebook.metadata.europa.notebook_id}`)
  await ready(page)
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await page.getByRole('menuitem', { name: 'Open project…' }).click()
  await expect(page).toHaveURL(/\/projects$/)
  await expect(page.getByRole('link', { name: 'Phase 3G activity project' })).toBeVisible()
  await capture(page, info, 'projects-1440x900')
  await page.getByRole('link', { name: 'Phase 3G activity project' }).click()
  const linkedNotebook = page.locator('#project-notebooks').getByRole('link', { name: 'Phase 3G library notebook' })
  await expect(linkedNotebook).toBeVisible()
  await capture(page, info, 'project-detail-1440x900')
  await linkedNotebook.click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${notebook.metadata.europa.notebook_id}$`))
  await expect(page.getByRole('heading', { name: 'Phase 3G library notebook' })).toBeVisible()
  await ready(page)
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await page.getByRole('menuitem', { name: 'Open notebook…' }).click()
  await expect(page).toHaveURL(/\/notebooks$/)
  await expect(page.getByRole('link', { name: 'Phase 3G library notebook' })).toBeVisible()
  await capture(page, info, 'notebook-library-1440x900')
  await page.goto('/activity')
  await page.getByLabel('Project').selectOption(project.id)
  await expect(page.getByText('No activity recorded for this project.')).toBeVisible()
  await capture(page, info, 'activity-empty-1440x900')
  await projectPost(request, `/projects/${project.id}/activities`, { project_id: project.id, notebook_id: notebook.metadata.europa.notebook_id, summary: 'Owner reviewed weekly observations', result: 'recorded' })
  await page.reload()
  await page.getByLabel('Project').selectOption(project.id)
  await expect(page.getByText('Owner reviewed weekly observations')).toBeVisible()
  await capture(page, info, 'activity-populated-1440x900')
})

test('library creation through execution, markdown, reorder, save/reopen, traceback and panels', async ({ page, request }) => {
  test.setTimeout(150000)
  await page.goto('/notebooks')
  await expect(page.getByRole('heading', { name: 'Notebooks' })).toBeVisible()
  page.once('dialog', dialog => dialog.accept('Phase 3G user workflow'))
  await page.getByRole('button', { name: 'New notebook', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Phase 3G user workflow' })).toBeVisible()
  const id = new URL(page.url()).pathname.split('/').at(-1)!
  await toolbar(page).getByRole('button', { name: 'Insert cell' }).click()
  await expect(page.locator('.cell')).toHaveCount(1)
  const editor = page.locator('.cell .cm-content').first()
  await editor.click(); await page.keyboard.insertText(fixture.one_cell)
  const firstExecution = page.waitForResponse(response => response.url().includes('/execute') && response.request().method() === 'POST')
  await toolbar(page).getByRole('button', { name: 'Run selected cell' }).click()
  const firstResponse = await firstExecution
  expect(firstResponse.status()).toBe(202)
  const first = (await (await request.get(`${api}/notebooks/${id}`)).json()).cells[0].id
  await settled(page, request, id, first, (await firstResponse.json()).execution_id, 'ok')
  await expect(page.locator('.cell .outputs')).toContainText('Hello, Europa!')
  await toolbar(page).getByRole('button', { name: 'Insert cell' }).click()
  await expect(page.locator('.cell')).toHaveCount(2)
  await page.locator('.cell').last().focus()
  page.once('dialog', dialog => dialog.accept())
  await toolbar(page).getByLabel('Cell type').selectOption('markdown')
  await expect(page.locator('.cell').last()).toHaveAttribute('aria-label', 'markdown cell 2')
  await page.locator('.cell').last().focus()
  await page.keyboard.press('Enter')
  await page.locator('.cell').last().locator('.cm-content').click()
  await page.keyboard.insertText(fixture.markdown)
  await toolbar(page).getByRole('button', { name: 'Save notebook' }).click()
  await expect(page.locator('.cell').last().locator('.markdown-preview h1')).toHaveText('Weekly observations')
  await page.getByRole('button', { name: 'Move cell 2 up' }).click()
  await expect(page.locator('.cell').first()).toHaveAttribute('aria-label', 'markdown cell 1')
  await ready(page)
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await page.getByRole('menuitem', { name: 'Open notebook…' }).click()
  await expect(page).toHaveURL(/\/notebooks$/)
  await page.getByRole('link', { name: 'Phase 3G user workflow' }).click()
  await expect(page.locator('.cell').first().locator('.markdown-preview h1')).toHaveText('Weekly observations')
  await expect(page.locator('.cell').nth(1).locator('.outputs')).toContainText('Hello, Europa!')
  await toolbar(page).getByRole('button', { name: 'Insert cell' }).click()
  await expect(page.locator('.cell')).toHaveCount(3)
  await expect(page.locator('.cell').last()).toHaveAttribute('aria-label', 'code cell 3')
  const last = (await (await request.get(`${api}/notebooks/${id}`)).json()).cells.at(-1).id
  const errorEditor = page.locator('.cell').last().locator('.cm-content')
  await errorEditor.click(); await page.keyboard.insertText(fixture.error)
  const errorExecution = page.waitForResponse(response => response.url().endsWith(`/cells/${last}/execute`) && response.request().method() === 'POST')
  await toolbar(page).getByRole('button', { name: 'Run selected cell' }).click()
  const errorResponse = await errorExecution
  expect(errorResponse.status()).toBe(202)
  await settled(page, request, id, last, (await errorResponse.json()).execution_id, 'error')
  await expect(page.locator('.cell').last().locator('.outputs')).toContainText('ValueError: Example invalid measurement')
  await toggleRoadmap(page)
  await expect(page.locator('#roadmap')).toBeVisible()
  await toggleRoadmap(page)
  await expect(page.locator('#roadmap')).toBeHidden()
  await expect(page.locator('.message-composer')).toBeVisible()
  await ready(page)
  await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
  await page.getByRole('menuitem', { name: 'Open notebook…' }).click()
  await expect(page).toHaveURL(/\/notebooks$/)
})

test('running receipt refuses library navigation until applied and operation settled', async ({ page, request }) => {
  test.setTimeout(100000)
  const root = realpathSync(process.env.EUROPA_TEST_TMP || '/private/tmp/europa-phase3g')
  const gate = join(root, `phase3g-release-${crypto.randomUUID()}`)
  const notebook = await post(request, '/notebooks', { title: 'Phase 3G pending navigation' })
  const id = notebook.metadata.europa.notebook_id
  // The kernel and the browser test share the same canonical host filesystem path.
  const source = `from pathlib import Path\nimport time\nrelease = Path(${JSON.stringify(gate)})\ndeadline = time.monotonic() + 35\nwhile not release.exists() and time.monotonic() < deadline:\n    time.sleep(0.05)\nif not release.exists():\n    raise TimeoutError('Phase 3G test release gate deadline')\nprint('gate released')`
  const doc = await add(request, id, 1, 'code', source)
  const cell = doc.cells[0].id
  let submitted = false
  let executionId: string | undefined
  try {
    await page.goto(`/notebooks/${id}`)
    await ready(page)
    await page.locator('.cm-content').click()
    await expect(toolbar(page).getByRole('button', { name: 'Run selected cell' })).toBeEnabled()
    const execution = page.waitForResponse(response => response.url().endsWith(`/cells/${cell}/execute`) && response.request().method() === 'POST')
    await toolbar(page).getByRole('button', { name: 'Run selected cell' }).click()
    submitted = true
    const response = await execution
    expect(response.status()).toBe(202)
    executionId = (await response.json()).execution_id
    await expect.poll(async () => {
      const current = await request.get(`${api}/notebooks/${id}/executions/${executionId}`)
      expect(current.ok()).toBe(true)
      return (await current.json() as Receipt).state
    }, { timeout: 30000 }).toBe('running')
    await expect(page.locator('footer.status')).toContainText('Pending execution receipt')
    await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
    await page.getByRole('menuitem', { name: 'Close notebook' }).click()
    await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
    await expect(page.getByRole('alert')).toContainText('still pending')
    await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
    await page.getByRole('menuitem', { name: 'Open project…' }).click()
    await expect(page).toHaveURL(new RegExp(`/notebooks/${id}$`))
    await expect(page.getByRole('alert')).toContainText('still pending')
    await expect(page.locator('.cell')).toHaveCount(1)
    writeFileSync(gate, 'release')
    await settled(page, request, id, cell, executionId, 'ok')
    await expect(page.locator('.outputs')).toContainText('gate released')
    await page.getByRole('menubar').getByRole('menuitem', { name: 'File' }).click()
    await page.getByRole('menuitem', { name: 'Open notebook…' }).click()
    await expect(page).toHaveURL(/\/notebooks$/)
  } finally {
    writeFileSync(gate, 'release')
    try {
      if (submitted) await expect.poll(async () => {
        const response = await request.get(`${api}/notebooks/${id}/executions${executionId ? `/${executionId}` : ''}`)
        expect(response.ok()).toBe(true)
        const body = await response.json()
        return executionId ? (body as Receipt).state : (body.items as Receipt[]).find(r => r.cell_id === cell)?.state
      }, { timeout: 45000 }).toBe('applied')
    } finally { rmSync(gate, { force: true }) }
  }
})
