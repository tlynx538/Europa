import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { test, expect } from '../browserFixtures'
import { agentServer } from '../browserAgentServer'

test('4B: Home approval survives reload, creates a physical notebook and reopens after restart', async ({ page, request }) => {
  const server = agentServer(request)
  const captures = join(process.env.EUROPA_PHASE4_CAPTURE_ROOT || resolve('../docs/screenshots/phase4'), '4b')
  const notebookPath = 'Revenue Forecasting/exploratory-analysis.ipynb'
  const objective = 'I have revenue data and want to forecast the next quarter.'
  mkdirSync(captures, { recursive: true })
  try {
    await server.start()
    await page.setViewportSize({ width:1440, height:900 })
    await page.goto(server.url)
    await expect(page.getByRole('tab', {name:'Files',exact:true})).toBeVisible()
    await page.getByRole('button', {name:'Ask Europa…'}).click()
    const drawer = page.getByRole('dialog', {name:'Ask Europa'})
    await drawer.getByLabel('Message to Europa').fill(objective)
    await drawer.getByRole('button', {name:'Send message'}).click()
    await expect(drawer.getByRole('heading', {name:'Review proposed action'})).toBeVisible()
    await expect(drawer.getByText(notebookPath, {exact:true})).toBeVisible()
    expect(existsSync(join(server.workspace,'Revenue Forecasting'))).toBe(false)
    await page.screenshot({path:join(captures,'home-approval-desktop.png')})
    await page.reload()
    await page.getByRole('button', {name:'Ask Europa…'}).click()
    await drawer.getByRole('button', {name:'Approve exact action'}).click()
    await expect(drawer.getByText('The approved project and notebook are saved.', {exact:false})).toBeVisible()
    const {metadata} = JSON.parse(readFileSync(join(server.workspace,notebookPath),'utf8'))
    const notebook = metadata.europa.notebook_id
    await drawer.getByRole('button', {name:'Open notebook'}).click()
    await expect(page).toHaveURL(new RegExp('/notebooks/'+notebook+'(?:[?#]|$)'))
    const conversation = await (await request.get(server.url+'/api/notebooks/'+notebook+'/conversation')).json()
    await server.stop()
    await server.start()
    await page.setViewportSize({width:390,height:844})
    await page.goto(server.url)
    await page.getByRole('tab', {name:'Running',exact:true}).click()
    await expect(page.getByText('No active notebook sessions.')).toBeVisible()
    await page.getByRole('tab', {name:'Files',exact:true}).click()
    await page.getByRole('button', {name:'Ask Europa…'}).click()
    await expect(drawer.getByText(objective,{exact:true})).toHaveCount(1)
    await expect(drawer.getByRole('button', {name:'Open notebook'})).toBeVisible()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:join(captures,'home-handoff-mobile.png')})
    await drawer.getByRole('button', {name:'Open notebook'}).click()
    await expect(page).toHaveURL(new RegExp('/notebooks/'+notebook+'(?:[?#]|$)'))
    expect((await (await request.get(server.url+'/api/notebooks/'+notebook+'/conversation')).json()).id).toBe(conversation.id)
  } finally { await server.stop() }
})
