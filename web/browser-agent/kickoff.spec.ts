import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

// Phase 4D Stage 1 kickoff questionnaire against the deterministic demo provider (not an LLM).
// Every wait is on persisted state: stream messages, approval cards, run panels and API records.
// The 4B journey in this suite already creates "Markov Chain" from the exact Markov sentence (and,
// extended for 4D, checks its kickoff questions); folders cannot be deleted through the API, so this
// journey asks about a "Markov chain model" to get its own project folder in the same disposable root.
const captures = resolve('../docs/screenshots/phase4d')
const desktop = { width: 1360, height: 900 }, mobile = { width: 390, height: 844 }
const MARKOV = ['Define a transition matrix', 'Simulate transitions', 'Estimate the stationary distribution', 'Visualize convergence']
const PURPOSE = 'I want to understand a simple Markov chain model.'
const ANSWER = 'I will simulate a three-state weather chain.'

const panel = (page: Page) => page.locator('details.home-coordinator')
const homeCoordinator = (page: Page) => page.locator('.home-thread [data-author="coordinator"]')
const owner = (page: Page) => page.locator('article.message[data-author="user"]')
const coordinator = (page: Page) => page.locator('article.message[data-author="coordinator"]')
const approval = (page: Page, title: string) => page.getByRole('region', { name: `Approval: ${title}` })
const runSummaries = (page: Page) => page.locator('.agent-stream details.agent-run > summary')

async function capture(page: Page, name: string, focus: Locator, size = desktop, block: 'start' | 'end' = 'end') {
  mkdirSync(captures, { recursive: true })
  const previous = page.viewportSize()
  await page.setViewportSize(size)
  await page.waitForFunction(width => document.documentElement.clientWidth === width, size.width)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await focus.evaluate((element, where) => element.scrollIntoView({ block: where }), block)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.screenshot({ path: join(captures, `${name}-${size === mobile ? 'mobile' : 'desktop'}.png`), animations: 'disabled' })
  if (previous) await page.setViewportSize(previous)
}

async function approve(page: Page, card: Locator) {
  const decided = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await card.getByRole('button', { name: 'Approve', exact: true }).click()
  const decision = await decided
  expect(decision.status()).toBe(200)
  expect(Object.keys(decision.request().postDataJSON()).sort()).toEqual(['decision', 'digest', 'operation_id'])
}

async function projectRoadmaps(request: APIRequestContext, title: string) {
  const projects = (await (await request.get('/api/projects')).json()).items as { id: string; data: { title: string } }[]
  const project = projects.find(item => item.data.title === title)
  expect(project).toBeTruthy()
  const response = await request.get(`/api/roadmaps?project_id=${project!.id}`)
  expect(response.ok()).toBe(true)
  return (await response.json()).items as { id: string; revision: number; data: { tasks: { id: string; title: string }[] } }[]
}

test('kickoff: questions and a draft plan after Open notebook, approve the draft, answer, revised plan bound to it', async ({ page, request }) => {
  test.setTimeout(180000)
  // Home: create the project notebook (the Home approval covers only the creation).
  await page.goto('/')
  await expect(panel(page)).toBeVisible()
  const fresh = page.waitForResponse(r => r.url().endsWith('/api/agent/home/conversations') && r.request().method() === 'POST')
  await panel(page).getByRole('button', { name: 'New', exact: true }).click()
  expect((await fresh).status()).toBe(201)
  const box = panel(page).getByRole('textbox', { name: 'Message the Coordinator' })
  await expect(box).toBeEnabled()
  await box.fill(PURPOSE)
  const started = page.waitForResponse(r => r.url().endsWith('/api/agent/runs') && r.request().method() === 'POST')
  await box.press('Enter')
  expect((await started).status()).toBe(201)
  const create = page.getByRole('region', { name: 'Approval: Create resources' })
  await expect(create.getByRole('listitem')).toHaveText(['Create project folder Markov Chain Model/', 'Create notebook Markov Chain Model/markov-chain-model.ipynb'])
  await approve(page, create)
  const created = homeCoordinator(page).filter({ hasText: 'Created Markov Chain Model/markov-chain-model.ipynb' })
  await expect(created).toHaveCount(1)
  await created.getByRole('button', { name: 'Open notebook' }).click()
  await expect(page).toHaveURL(/\/notebooks\/[a-f0-9]{32}$/)

  // Notebook: origin message, then the Coordinator's questions marked "Needs your input", then the draft plan card.
  const origin = coordinator(page).filter({ hasText: 'Notebook created from a Home conversation' })
  await expect(origin).toHaveCount(1)
  const questions = coordinator(page).filter({ has: page.locator('.needs-input-badge') })
  await expect(questions).toHaveCount(1)
  await expect(questions.locator('.needs-input-badge')).toHaveText('Needs your input')
  const lines = (await questions.locator('p').innerText()).split('\n').filter(line => /^\d+\. /.test(line))
  expect(lines.length).toBeGreaterThanOrEqual(3)
  expect(lines.length).toBeLessThanOrEqual(5)
  expect(lines.map(line => line.split('.')[0])).toEqual(lines.map((_, index) => String(index + 1)))
  for (const line of lines) expect(line).toContain('Markov chain model')
  const draft = approval(page, 'Create roadmap')
  await expect(draft).toBeVisible()
  await expect(draft.locator('.agent-task-title')).toHaveText(MARKOV)
  await expect(draft).toContainText('answers to the questions can refine it')
  // Order: origin → questions → kickoff run panel holding the draft card.
  expect(await questions.evaluate(element => element.nextElementSibling?.classList.contains('agent-stream') ?? false)).toBe(true)
  expect(await questions.evaluate((element, title) => !!element.nextElementSibling?.querySelector(`[aria-label="${title}"]`), 'Approval: Create roadmap')).toBe(true)
  expect(await origin.evaluate((element, id) => element.nextElementSibling?.id === id, await questions.getAttribute('id'))).toBe(true)
  // One-shot reveal: the composer is focused and invites an answer.
  const composer = page.getByRole('textbox', { name: 'Message' })
  await expect(composer).toBeFocused()
  await expect(composer).toHaveAttribute('placeholder', "Answer the Coordinator's questions…")
  const notebook = new URL(page.url()).pathname.split('/').at(-1)!
  expect(await projectRoadmaps(request, 'Markov Chain Model')).toEqual([])  // nothing saved before approval
  // Questions (with their badge) at the top, followed by the draft plan card.
  await capture(page, 'kickoff', questions, desktop, 'start')
  await capture(page, 'kickoff', questions, mobile, 'start')

  // Approve the draft before answering.
  await approve(page, draft)
  await expect(coordinator(page).filter({ hasText: 'Saved the draft roadmap; answer the questions above and I will refine it.' })).toHaveCount(1)
  const [saved] = await projectRoadmaps(request, 'Markov Chain Model')
  expect(saved.revision).toBe(1)
  expect(saved.data.tasks.map(task => task.title)).toEqual(MARKOV)
  await expect(composer).toHaveAttribute('placeholder', "Answer the Coordinator's questions…")

  // Answer one question through the normal composer: a normal Coordinator run proposes a revision.
  await composer.fill(ANSWER)
  const posted = page.waitForResponse(r => /\/api\/conversations\/[a-f0-9]{32}\/messages$/.test(r.url()) && r.request().method() === 'POST')
  const run = page.waitForResponse(r => r.url().endsWith('/api/agent/runs') && r.request().method() === 'POST')
  await composer.press('Enter')
  expect((await posted).status()).toBe(201)
  const answerRun = await run
  expect(answerRun.status()).toBe(201)
  expect(Object.keys(answerRun.request().postDataJSON()).sort()).toEqual(['conversation_id', 'message_id', 'operation_id'])
  await expect(owner(page).filter({ hasText: ANSWER })).toHaveCount(1)
  await expect(composer).toHaveAttribute('placeholder', 'Write a message...')
  const revise = approval(page, 'Revise roadmap')
  await expect(revise).toBeVisible()
  await expect(revise.getByText(`Bound to roadmap revision ${saved.revision}`)).toBeVisible()
  await expect(revise.locator('.agent-task-title')).toHaveText([...MARKOV, `Use your answer: ${ANSWER}`])
  await capture(page, 'kickoff-revise', revise.getByRole('button', { name: 'Approve', exact: true }))
  await approve(page, revise)
  await expect(coordinator(page).filter({ hasText: 'Saved the roadmap with 5 tasks.' })).toHaveCount(1)
  const [revised] = await projectRoadmaps(request, 'Markov Chain Model')
  expect(revised.id).toBe(saved.id)
  expect(revised.revision).toBe(2)
  expect(revised.data.tasks.slice(0, 4).map(task => task.id)).toEqual(saved.data.tasks.map(task => task.id))

  // After a reload both runs are settled; the questions keep their badge but the answer closed them.
  await page.reload()
  await expect(runSummaries(page)).toHaveText(['Completed · 2 actions', 'Completed · 1 action'])
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  await expect(coordinator(page).locator('.needs-input-badge')).toHaveCount(1)
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveAttribute('placeholder', 'Write a message...')
  expect(new URL(page.url()).pathname).toBe(`/notebooks/${notebook}`)
})
