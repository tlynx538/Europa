import { useEffect, useState } from 'react'
import { agentConfig, agentConversation, agentHandoffs, agentMessages, agentRuns, liveRun, type AgentConfig, type AgentRun, type AgentScope, type Handoff } from './agentApi'
import type { Message, RecordData } from './applicationApi'

type Feed = { config?: AgentConfig; conversation?: string; runs: AgentRun[]; messages: RecordData<Message>[]; handoffs: Handoff[]; before: number | null; error: string }
const empty: Feed = {runs:[],messages:[],handoffs:[],before:null,error:''}

export function useAgentFeed(scope: AgentScope, enabled: boolean, knownConversation?: string) {
  const key = JSON.stringify(scope)
  const [feed, setFeed] = useState<Feed>(empty)
  const [older, setOlder] = useState<RecordData<Message>[]>([])
  const [olderCursor, setOlderCursor] = useState<number | null>()
  const [refresh, setRefresh] = useState(0)
  useEffect(() => { setFeed(empty); setOlder([]); setOlderCursor(undefined) }, [key])
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    const { signal } = controller
    let timer: ReturnType<typeof setTimeout>
    async function start() {
      const [config, conversation] = await Promise.all([agentConfig(signal),knownConversation ? {id:knownConversation} : agentConversation(JSON.parse(key),signal)])
      for (let attempt = 0; !signal.aborted; attempt++) {
        const [runs,messages,handoffs] = await Promise.all([agentRuns(conversation.id,signal),agentMessages(conversation.id,0,signal),knownConversation ? {items:[]} : agentHandoffs(conversation.id,signal)])
        if (signal.aborted) return
        const active = runs.items.some(run => liveRun(run.state))
        setFeed({config,conversation:conversation.id,runs:runs.items,messages:messages.items,handoffs:handoffs.items,before:messages.next_before,error:active && attempt>=120 ? 'Updates paused. Refresh to reconnect.' : ''})
        if (!active || attempt>=120) return
        await new Promise<void>(resolve => {
          const done = () => { clearTimeout(timer); signal.removeEventListener('abort',done); resolve() }
          timer = setTimeout(done,1250)
          signal.addEventListener('abort',done,{once:true})
        })
      }
    }
    void start().catch(() => { if (!signal.aborted) setFeed(previous => ({...previous,error:'Conversation unavailable. Refresh to reconnect.'})) })
    return () => controller.abort()
  }, [enabled,key,knownConversation,refresh])
  const before = olderCursor === undefined ? feed.before : olderCursor
  const loadOlder = async () => {
    if (!before || !feed.conversation) return
    try {
      const page = await agentMessages(feed.conversation,before)
      setOlder(previous => [...page.items,...previous].filter((item,i,items)=>items.findIndex(other=>other.id===item.id)===i))
      setOlderCursor(page.next_before)
    } catch { setFeed(previous=>({...previous,error:'Older messages unavailable.'})) }
  }
  return {...feed,messages:[...older.filter(item=>!feed.messages.some(message=>message.id===item.id)),...feed.messages],before,loadOlder,reload:()=>setRefresh(value=>value+1)}
}
