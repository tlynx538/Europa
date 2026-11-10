import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { test, expect } from '../browserFixtures'
import { agentServer } from '../browserAgentServer'

test('4A: browser-origin run and read tool persist through a real backend restart', async ({ page, request }, info) => {
  const server=agentServer(request)
  const captures=join(process.env.EUROPA_PHASE4_CAPTURE_ROOT || resolve('../docs/screenshots/phase4'),'4a')
  mkdirSync(captures,{recursive:true})
  try {
    await server.start()
    await page.setViewportSize({width:1440,height:900})
    await page.goto(server.url)
    await expect(page.getByRole('tab',{name:'Files',exact:true})).toBeVisible()
    await expect(page.getByText('This folder is empty.',{exact:true})).toBeVisible()
    const body={operation_id:crypto.randomUUID(),content:'I want to study Markov chains. What should I learn first?'}
    const accepted=await page.evaluate(async body => {
      const response=await fetch('/api/agents/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      return {status:response.status,...await response.json()}
    },body)
    expect(accepted.status).toBe(202)
    await page.reload()
    await expect.poll(async () => (await (await request.get(`${server.url}/api/agents/runs/${accepted.id}`)).json()).state).toBe('completed')
    const events=await (await request.get(`${server.url}/api/agents/runs/${accepted.id}/events`)).json()
    expect(events.items.map((event:{kind:string})=>event.kind)).toContain('tool_completed')
    const history=await (await request.get(`${server.url}/api/conversations/${accepted.conversation_id}/messages`)).json()
    expect(history.items.map((item:{data:{author_role:string}})=>item.data.author_role)).toEqual(['user','coordinator'])
    expect(history.items[1].data.content).toContain('stationary distributions')
    await page.screenshot({path:join(captures,'home-desktop.png'),fullPage:true})
    await server.stop(); await server.start()
    await page.setViewportSize({width:390,height:844}); await page.reload()
    await expect(page.getByRole('tab',{name:'Files',exact:true})).toBeVisible()
    await expect(page.getByText('This folder is empty.',{exact:true})).toBeVisible()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    const reopened=await page.evaluate(async id => (await fetch(`/api/conversations/${id}/messages`)).json(),accepted.conversation_id)
    expect(reopened).toEqual(history)
    expect(await (await request.get(`${server.url}/api/agents/runs/${accepted.id}/events`)).json()).toEqual(events)
    const replay=await request.post(`${server.url}/api/agents/runs`,{data:body})
    expect(await replay.json()).toEqual({id:accepted.id,conversation_id:accepted.conversation_id,message_id:accepted.message_id})
    const rejected=await request.post(`${server.url}/api/agents/runs`,{data:{...body,operation_id:crypto.randomUUID()},headers:{Origin:'https://foreign.invalid'}})
    expect(rejected.status()).toBe(403)
    await page.screenshot({path:join(captures,'home-mobile-after-restart.png'),fullPage:true})
    await info.attach('foundation-recovery',{body:JSON.stringify({sameHistory:true,sameEvents:true,idempotent:true,provider:'fake',crossOriginDenied:true}),contentType:'application/json'})
  } finally { await server.stop() }
})
