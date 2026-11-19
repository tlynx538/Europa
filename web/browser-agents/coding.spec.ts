import { mkdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { test, expect } from '../browserFixtures'
import { agentServer } from '../browserAgentServer'

test('4D: approved Home-to-Markov workflow saves an actual Gateway receipt and reopens', async ({page,request},info)=>{
  const server = agentServer(request)
  const captures = join(process.env.EUROPA_PHASE4_CAPTURE_ROOT || resolve('../docs/screenshots/phase4'),'4d')
  mkdirSync(captures,{recursive:true})
  try {
    await server.start()
    await page.setViewportSize({width:1440,height:900})
    await page.goto(server.url)
    await page.getByRole('button',{name:'Ask Europa…'}).click()
    const drawer = page.getByRole('dialog',{name:'Ask Europa'})
    await drawer.getByLabel('Message to Europa').fill('I want to understand a simple Markov chain.')
    await drawer.getByRole('button',{name:'Send message'}).click()
    await drawer.getByRole('button',{name:'Approve exact action'}).click()
    const notebookPath = join(server.workspace,'Markov Chains','markov-chain.ipynb')
    await drawer.getByRole('button',{name:'Open notebook'}).click()
    const notebook = JSON.parse(readFileSync(notebookPath,'utf8')).metadata.europa.notebook_id
    await expect(page).toHaveURL(new RegExp('/notebooks/'+notebook+'$'))
    const api = server.url+'/api'
    const canonical = (await (await request.get(api+'/notebooks/'+notebook+'/conversation')).json()).id
    await page.getByRole('textbox',{name:'Message',exact:true}).fill('Plan the simple Markov chain notebook.')
    await page.getByRole('button',{name:'Ask Coordinator'}).click()
    await page.getByRole('button',{name:'Approve exact action'}).click()
    await expect(page.getByText('The approved roadmap is saved in this notebook.')).toBeVisible()

    const toggleRoadmap = async () => {
      await page.getByRole('menubar').getByRole('menuitem',{name:'View'}).click()
      await page.getByRole('menuitemcheckbox',{name:'Roadmap panel'}).click()
    }
    await toggleRoadmap()
    await page.locator('#roadmap').getByRole('button',{name:'Assign to Coding Agent'}).click()
    await toggleRoadmap()
    await page.getByRole('button',{name:'Approve exact action'}).click()
    const coding = page.getByRole('region',{name:'Coding Agent activity'})
    await expect(coding.getByText(/Each row is the distribution/)).toBeVisible()
    await coding.getByRole('heading',{name:'Review proposed action'}).scrollIntoViewIfNeeded()
    await page.screenshot({path:join(captures,'code-proposal-desktop.png')})
    await coding.getByRole('button',{name:'Approve exact action'}).click()
    await expect(coding.getByText(/Run the saved Python cell/)).toBeVisible()
    await expect(coding.getByRole('button',{name:'Approve exact action'})).toBeEnabled()
    expect((await (await request.get(api+'/notebooks/'+notebook+'/executions')).json()).items).toEqual([])
    await page.reload()
    await coding.getByRole('button',{name:'Approve exact action'}).click()
    await expect(page.locator('.cell .outputs')).toContainText('[1.0, 1.0]')
    await expect(coding.getByRole('button',{name:'Coding Agent · Completed'})).toBeVisible()
    const runs = (await (await request.get(api+'/agents/runs?conversation_id='+canonical)).json()).items
    const child = runs.find((run:{role:string})=>run.role==='coding_agent')
    const receipts = (await (await request.get(api+'/agents/runs/'+child.id+'/executions')).json()).items
    expect(receipts).toHaveLength(1)
    expect(receipts[0]).toMatchObject({state:'applied',status:'ok'})
    expect(receipts[0].outputs[0].text).toBe('[1.0, 1.0]\n')
    const receiptId = receipts[0].execution_id
    const receipt = (await (await request.get(api+'/notebooks/'+notebook+'/executions/'+receiptId)).json())
    expect(receipt.source_hash).toBe(receipts[0].source_hash)
    const privateReceipt = JSON.parse(readFileSync(join(server.root,'notebooks',notebook+'.'+receiptId+'.execution.json'),'utf8'))
    expect(privateReceipt.kernel_id).toBeTruthy()
    await page.getByRole('link',{name:'Europa',exact:true}).click()
    await expect(page).toHaveURL(server.url+'/')
    await server.stop(); await server.start()
    await page.setViewportSize({width:390,height:844})
    await page.reload()
    await page.getByRole('button',{name:'Recent notebooks'}).click()
    await page.getByRole('menuitem',{name:/markov-chain/}).click()
    await expect(page).toHaveURL(new RegExp('/notebooks/'+notebook+'$'))
    await expect(page.locator('.cell .outputs')).toContainText('[1.0, 1.0]')
    await expect(page.getByText(/The applied Gateway receipt/)).toBeVisible()
    expect((await (await request.get(api+'/notebooks/'+notebook+'/conversation')).json()).id).toBe(canonical)
    expect((await (await request.get(api+'/agents/runs/'+child.id+'/executions')).json()).items).toEqual(receipts)
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.locator('.cell .outputs').scrollIntoViewIfNeeded()
    await page.screenshot({path:join(captures,'saved-output-mobile.png')})
    await info.attach('verified-receipt',{body:JSON.stringify({notebook,run:child.id,receipt:receiptId,status:receipt.state,source_hash:receipt.source_hash}),contentType:'application/json'})
  } finally { await server.stop() }
})
