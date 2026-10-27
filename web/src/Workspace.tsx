import { useCallback, useEffect, useState } from 'react'
import { json, request } from './api'

type RecordData = { id: string; revision: number; notebook_id: string | null; data: any }
type Page = { items: RecordData[]; next_cursor: number | null }
type Props = { notebookId?: string; cells: { id: string }[]; onOpenNotebook: (id: string, cell?: string) => void; onCommentCounts: (counts: Record<string, number>) => void }
const operation = () => crypto.randomUUID()

async function all(path: string): Promise<RecordData[]> {
  let cursor: number | null = 0
  const records: RecordData[] = []
  do {
    const page: Page = await request(`${path}${path.includes('?') ? '&' : '?'}after=${cursor}&limit=100`)
    records.push(...page.items); cursor = page.next_cursor
  } while (cursor !== null)
  return records
}

export function Workspace({ notebookId, cells, onOpenNotebook, onCommentCounts }: Props) {
  const [projects, setProjects] = useState<RecordData[]>([])
  const [project, setProject] = useState<RecordData | null>(null)
  const [roadmaps, setRoadmaps] = useState<RecordData[]>([])
  const [conversations, setConversations] = useState<RecordData[]>([])
  const [messages, setMessages] = useState<RecordData[]>([])
  const [comments, setComments] = useState<RecordData[]>([])
  const [activities, setActivities] = useState<RecordData[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const loadProjects = useCallback(async () => setProjects(await all('/projects')), [])
  const loadProject = useCallback(async (id: string) => {
    const [p, r, c, a] = await Promise.all([
      request<RecordData>(`/projects/${id}`), all(`/roadmaps?project_id=${id}`),
      all(`/conversations?project_id=${id}`), all(`/projects/${id}/activities`),
    ])
    setProject(p); setRoadmaps(r); setConversations(c); setActivities(a)
  }, [])
  const loadComments = useCallback(async () => {
    if (notebookId) setComments(await all(`/notebooks/${notebookId}/comments`)); else setComments([])
  }, [notebookId])
  const loadMessages = useCallback(async (id: string) => setMessages(await all(`/conversations/${id}/messages`)), [])
  useEffect(() => { void loadProjects().catch(e => setError(String(e))) }, [loadProjects])
  useEffect(() => { setComments([]); void loadComments().catch(e => setError(String(e))) }, [loadComments])
  useEffect(() => {
    const counts: Record<string, number> = {}
    for (const comment of comments) if (comment.data.project_id === project?.id && !comment.data.resolved) counts[comment.data.cell_id] = (counts[comment.data.cell_id] ?? 0) + 1
    onCommentCounts(counts)
  }, [comments, project?.id, onCommentCounts])
  const conversation = conversations[0]
  useEffect(() => {
    setMessages([])
    if (conversation) void loadMessages(conversation.id).catch(e => setError(String(e)))
  }, [conversation?.id, loadMessages])
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError('')
    try { await fn() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const goCell = (cell: string) => document.getElementById(`cell-${cell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  const member = !!project?.data.notebooks.some((n: { notebook_id: string; present: boolean }) => n.notebook_id === notebookId && n.present)
  return <div className="workspace">
    <section className="roadmap-panel">
      <h2>Project &amp; roadmap</h2>
      {error && <p role="alert" className="alert">{error}</p>}
      <button disabled={busy} onClick={() => void run(async () => {
        const title = prompt('Project title')?.trim(); if (!title) return
        const p = await request<RecordData>('/projects', json('POST', { operation_id: operation(), title }))
        await loadProjects(); await loadProject(p.id)
      })}>New project</button>
      <select aria-label="Project" value={project?.id ?? ''} disabled={busy} onChange={e => {
        if (e.target.value) void run(() => loadProject(e.target.value)); else { setProject(null); setRoadmaps([]); setConversations([]); setActivities([]) }
      }}><option value="">Select project</option>{projects.map(p => <option value={p.id} key={p.id}>{p.data.title}</option>)}</select>
      {project && <>
        {notebookId && !member && <button disabled={busy} onClick={() => void run(async () => {
          await request(`/projects/${project.id}/notebooks`, json('POST', { operation_id: operation(), revision: project.revision, notebook_id: notebookId }))
          await loadProject(project.id)
        })}>Add open notebook to project</button>}
        <p>Project notebooks</p>{project.data.notebooks.filter((n: { present: boolean }) => n.present).map((n: { notebook_id: string }) => <button key={n.notebook_id} onClick={() => onOpenNotebook(n.notebook_id)}>Open {n.notebook_id.slice(0, 8)}</button>)}
        <button disabled={busy} onClick={() => void run(async () => {
          const objective = prompt('Roadmap objective')?.trim(); if (!objective) return
          const title = prompt('First task title')?.trim(); if (!title) return
          const criterion = prompt('Acceptance criterion')?.trim(); if (!criterion) return
          await request('/roadmaps', json('POST', { operation_id: operation(), project_id: project.id, ...(member ? { notebook_id: notebookId } : {}), objective,
            tasks: [{ id: crypto.randomUUID(), title, acceptance_criteria: [criterion] }] }))
          await loadProject(project.id)
        })}>Create roadmap</button>
        {roadmaps.map(r => <div key={r.id}><h3>{r.data.objective}</h3><div className="progress-label"><span>Overall progress</span><span>{r.data.progress}%</span></div><progress max="100" value={r.data.progress} />
          {r.data.tasks.map((task: any) => <div className="task" key={task.id}>
            <button onClick={() => { if (task.links?.notebook_id && task.links.notebook_id !== notebookId) onOpenNotebook(task.links.notebook_id, task.links.cell_id); else if (task.links?.cell_id) goCell(task.links.cell_id) }}>
              {task.display.checkmark ? '✓ ' : ''}{task.title} · {task.status.replace('_', ' ')}</button>
            {task.display.show_progress && <><div>{task.progress}%</div><progress max="100" value={task.progress} /></>}
            {task.explanation && <p>{task.explanation}</p>}
            <button disabled={busy} onClick={() => void run(async () => {
              const status = prompt('Status: pending, in_progress, blocked, needs_input, completed, cancelled', task.status)
              if (!status || !['pending', 'in_progress', 'blocked', 'needs_input', 'completed', 'cancelled'].includes(status)) return
              const explanation = ['blocked', 'needs_input'].includes(status) ? prompt('Explanation')?.trim() : null
              if (['blocked', 'needs_input'].includes(status) && !explanation) return
              const summary = status === 'completed' ? prompt('Owner acceptance evidence')?.trim() : null
              if (status === 'completed' && !summary) return
              const { display, progress, accepted_by, ...stored } = task
              void display; void progress; void accepted_by
              const updated = { ...stored, status, explanation, accepted: status === 'completed', evidence: summary ? [{ summary }] : [] }
              await request(`/roadmaps/${r.id}/tasks/${task.id}`, json('PUT', { operation_id: operation(), revision: r.revision, task: updated }))
              await loadProject(project.id)
            })}>Update status</button>
          </div>)}
        </div>)}
        {activities.length > 0 && <><h3>Activity</h3>{activities.map(a => <p key={a.id}>{a.data.summary}</p>)}</>}
      </>}
    </section>
    <section className="discussion-panel" id="discussion"><h2>Discussion</h2>
      {!project ? <p>Select a project to discuss work.</p> : <>
        {!conversation && <button disabled={busy} onClick={() => void run(async () => {
          await request('/conversations', json('POST', { operation_id: operation(), project_id: project.id, title: 'Discussion' }))
          await loadProject(project.id)
        })}>Start discussion</button>}
        {messages.map(m => <p className="message" key={m.id}><strong>{m.data.author_role}</strong><br />{m.data.content}</p>)}
        {conversation && <form onSubmit={e => { e.preventDefault(); if (!message.trim()) return; void run(async () => {
          await request(`/conversations/${conversation.id}/messages`, json('POST', { operation_id: operation(), content: message }))
          setMessage(''); await loadMessages(conversation.id)
        }) }}><label>Message<textarea value={message} onChange={e => setMessage(e.target.value)} /></label><button disabled={busy || !message.trim()}>Send</button></form>}
        {member && <><h3>Cell comments</h3><button disabled={busy || !cells.length} onClick={() => void run(async () => {
          const cell = prompt(`Cell ID (${cells.map(c => c.id).join(', ')})`); if (!cell || !cells.some(c => c.id === cell)) return
          const content = prompt('Comment')?.trim(); if (!content) return
          await request(`/notebooks/${notebookId}/comments`, json('POST', { operation_id: operation(), project_id: project.id, notebook_id: notebookId, cell_id: cell, content }))
          await loadComments()
        })}>Comment on cell</button>
        {comments.filter(c => c.data.project_id === project.id).map(c => <div className="message" key={c.id}>
          <button onClick={() => goCell(c.data.cell_id)}>Cell {c.data.cell_id} · {c.data.anchor_status}</button><p>{c.data.content}</p><span>{c.data.resolved ? 'Resolved' : 'Open'}</span>
          {!c.data.parent_id && <button disabled={busy} onClick={() => void run(async () => {
            await request(`/comments/${c.id}`, json('PATCH', { operation_id: operation(), revision: c.revision, resolved: !c.data.resolved })); await loadComments()
          })}>{c.data.resolved ? 'Reopen' : 'Resolve'}</button>}
          {!c.data.parent_id && <button disabled={busy} onClick={() => void run(async () => {
            const content = prompt('Reply')?.trim(); if (!content) return
            await request(`/notebooks/${notebookId}/comments`, json('POST', { operation_id: operation(), project_id: project.id, notebook_id: notebookId, cell_id: c.data.cell_id, parent_id: c.id, content }))
            await loadComments()
          })}>Reply</button>}
        </div>)}</>}
      </>}
      <p>Uploads are not yet available.</p><p>Coordinator controls are not implemented.</p>
    </section>
  </div>
}
