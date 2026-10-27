import { useCallback, useEffect, useRef, useState } from 'react'
import { SidePanel, usePanels } from './Layout'
import { TaskEditor } from './TaskEditor'
import { all, change, pendingMutation, retryMutation, rawTask, type RecordData, type Project, type Roadmap, type Conversation, type Message, type Comment, type Activity, type Task } from './applicationApi'
import { request } from './api'

type Props = { notebookId?: string; cells: { id: string }[]; selectedCell?: string; showActivity: boolean; revision?: number; onOpenNotebook: (id: string, cell?: string) => void; onCommentCounts: (counts: Record<string, number>) => void }

export function Workspace({ notebookId, cells, selectedCell, showActivity, revision, onOpenNotebook, onCommentCounts }: Props) {
  const panels = usePanels()
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [project, setProject] = useState<RecordData<Project> | null>(null)
  const [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([]), [conversations, setConversations] = useState<RecordData<Conversation>[]>([])
  const [messages, setMessages] = useState<RecordData<Message>[]>([]), [comments, setComments] = useState<RecordData<Comment>[]>([])
  const [activities, setActivities] = useState<RecordData<Activity>[]>([])
  const [conversationId, setConversationId] = useState(''), [message, setMessage] = useState(''), [reply, setReply] = useState<string | null>(null), [needsInput, setNeedsInput] = useState(false)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pending, setPending] = useState(!!pendingMutation())
  const [editing, setEditing] = useState<{ roadmap: RecordData<Roadmap>; task: Task; newTask: boolean } | null>(null)
  const [taskFilter, setTaskFilter] = useState<string | null>(null)
  const generation = useRef(0), locked = useRef(false)
  const currentNotebook = useRef(notebookId); currentNotebook.current = notebookId
  const loadProject = useCallback(async (id: string) => {
    const token = ++generation.current
    const [p, r, c, a] = await Promise.all([request<RecordData<Project>>(`/projects/${id}`), all<Roadmap>(`/roadmaps?project_id=${id}`), all<Conversation>(`/conversations?project_id=${id}`), all<Activity>(`/projects/${id}/activities`)])
    if (generation.current !== token) return
    setProject(p); setRoadmaps(r); setConversations(c); setActivities(a)
    setConversationId(previous => c.some(item => item.id === previous) ? previous : c[0]?.id ?? '')
    localStorage.setItem('europa.project', id)
  }, [])
  useEffect(() => {
    void all<Project>('/projects').then(setProjects).catch(e => setError(String(e)))
    const last = localStorage.getItem('europa.project'); if (last) void loadProject(last).catch(e => setError(String(e)))
  }, [loadProject])
  const loadComments = useCallback(async () => notebookId ? all<Comment>(`/notebooks/${notebookId}/comments`) : [], [notebookId])
  const refreshComments = async () => { const records = await loadComments(); if (currentNotebook.current === notebookId) setComments(records) }
  useEffect(() => { let stale = false; setComments([]); void loadComments().then(c => { if (!stale) setComments(c) }).catch(e => { if (!stale) setError(String(e)) }); return () => { stale = true } }, [loadComments, revision])
  useEffect(() => {
    const counts: Record<string, number> = {}
    for (const c of comments) if (c.data.project_id === project?.id && !c.data.resolved && !c.data.parent_id) counts[c.data.cell_id] = (counts[c.data.cell_id] ?? 0) + 1
    onCommentCounts(counts)
  }, [comments, project?.id, onCommentCounts])
  useEffect(() => {
    let stale = false; setMessages([]); setReply(null)
    if (conversationId) void all<Message>(`/conversations/${conversationId}/messages`).then(m => { if (!stale) setMessages(m) }).catch(e => { if (!stale) setError(String(e)) })
    return () => { stale = true }
  }, [conversationId])
  async function refresh() {
    setProjects(await all<Project>('/projects'))
    if (project) await loadProject(project.id)
    await refreshComments()
    if (conversationId) setMessages(await all<Message>(`/conversations/${conversationId}/messages`))
  }
  async function run(fn: () => Promise<void>) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError('')
    try { await fn() } catch (e) { setError(String(e)) } finally { locked.current = false; setBusy(false); setPending(!!pendingMutation()) }
  }
  const member = project?.data.notebooks.some(n => n.notebook_id === notebookId && n.present)
  const blankTask = (): Task => ({ id: crypto.randomUUID(), title: '', acceptance_criteria: [''], parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1,
    links: member ? { notebook_id: notebookId, cell_id: selectedCell ?? null } : {}, evidence: [], accepted: false })
  const commentCell = selectedCell ?? cells[0]?.id
  const jump = (notebook: string | null | undefined, cell?: string | null) => {
    if (notebook) onOpenNotebook(notebook, cell ?? undefined)
    if (panels.mobile) { panels.setLeft(false); panels.setRight(false) }
  }
  return <div className="workspace">
    <SidePanel side="left"><h2>Project &amp; roadmap</h2>
      {error && <p role="alert" className="alert">{error}</p>}
      {pending && <div className="alert"><p>An operation has an unknown outcome. Retry the identical operation safely; no new operation ID will be created.</p><button disabled={busy} onClick={() => void run(async () => { await retryMutation(); await refresh() })}>Retry pending operation</button></div>}
      <button disabled={busy || pending} onClick={() => void run(async () => { const title = prompt('Project title')?.trim(); if (!title) return; const p = await change<RecordData<Project>>('/projects', 'POST', { title }); setProjects(await all<Project>('/projects')); await loadProject(p.id) })}>New project</button>
      <select aria-label="Project" value={project?.id ?? ''} disabled={busy} onChange={e => { if (e.target.value) void run(() => loadProject(e.target.value)); else { ++generation.current; setProject(null); setRoadmaps([]); setConversations([]); setConversationId(''); setActivities([]); localStorage.removeItem('europa.project') } }}><option value="">Select project</option>{projects.map(p => <option value={p.id} key={p.id}>{p.data.title}</option>)}</select>
      {project && <>
        <button disabled={busy} onClick={() => void run(refresh)}>Refresh project history</button>
        {notebookId && !member && <button disabled={busy || pending} onClick={() => void run(async () => { await change(`/projects/${project.id}/notebooks`, 'POST', { revision: project.revision, notebook_id: notebookId }); await loadProject(project.id) })}>Add open notebook to project</button>}
        <details><summary>Project notebooks</summary>{project.data.notebooks.map(n => <button key={n.notebook_id} disabled={!n.present} onClick={() => jump(n.notebook_id)}>Open {n.notebook_id.slice(0, 8)}{!n.present ? ' (missing)' : ''}</button>)}</details>
        <button disabled={busy || pending} onClick={() => void run(async () => {
          const objective = prompt('Roadmap objective')?.trim(); if (!objective) return
          const title = prompt('First task title')?.trim(); if (!title) return
          const criterion = prompt('Acceptance criterion')?.trim(); if (!criterion) return
          await change('/roadmaps', 'POST', { project_id: project.id, ...(member ? { notebook_id: notebookId } : {}), objective, tasks: [{ ...blankTask(), title, acceptance_criteria: [criterion] }] }); await loadProject(project.id)
        })}>Create roadmap</button>
        {roadmaps.map(r => <section className="roadmap" key={r.id}><h3>{r.data.objective}</h3><div className="progress-label"><span>Overall progress</span><span>{r.data.progress}%</span></div><progress aria-label="Overall project progress" max="100" value={r.data.progress} />
          <button disabled={busy || pending} onClick={() => { setEditing({ roadmap: r, task: blankTask(), newTask: true }) }}>Add task</button>
          <button disabled={busy || pending} onClick={() => void run(async () => { const objective = prompt('Roadmap objective', r.data.objective)?.trim(); if (!objective) return; await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective, tasks: r.data.tasks.map(rawTask) }); await loadProject(project.id) })}>Edit objective</button>
          <ol>{r.data.tasks.map((task, index) => <li className={`task ${task.display.compact ? 'compact' : ''}`} key={task.id}>
            <button onClick={() => { setTaskFilter(task.id); jump(task.links.notebook_id, task.links.cell_id) }}>{task.display.checkmark ? '✓ ' : ''}{task.title} · {task.status.replace('_', ' ')}</button>
            {task.display.show_progress && <><span>{task.progress}%</span><progress aria-label={`${task.title} progress`} max="100" value={task.progress} /></>}
            {task.explanation && <p>{task.explanation}</p>}
            <details><summary>Task details</summary><ul>{task.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>{task.evidence.map((e, i) => <p key={i}>{e.summary}</p>)}
              <button disabled={busy || pending} onClick={() => setEditing({ roadmap: r, task: rawTask(task), newTask: false })}>Edit task</button>
              <button disabled={busy || pending || index === 0} onClick={() => void run(async () => { const tasks = r.data.tasks.map(rawTask); [tasks[index - 1], tasks[index]] = [tasks[index], tasks[index - 1]]; await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective: r.data.objective, tasks }); await loadProject(project.id) })}>Move task up</button>
              <button disabled={busy || pending} onClick={() => { if (confirm('Remove task from the current plan? Historical versions are retained.')) void run(async () => { await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective: r.data.objective, tasks: r.data.tasks.filter(t => t.id !== task.id).map(rawTask) }); await loadProject(project.id) }) }}>Remove task</button>
            </details>
          </li>)}</ol>
        </section>)}
        {(showActivity || taskFilter) && <section><h3>Contextual activity</h3>{taskFilter && <button onClick={() => setTaskFilter(null)}>Clear task filter</button>}
          {activities.filter(a => taskFilter ? a.data.task_id === taskFilter : a.data.notebook_id === notebookId && (!a.data.cell_id || a.data.cell_id === selectedCell)).map(a => <p key={a.id}><button onClick={() => jump(a.data.notebook_id, a.data.cell_id)}>{a.data.summary}</button> · {a.data.result}</p>)}
          <button disabled={busy || pending} onClick={() => void run(async () => { const summary = prompt('Owner activity summary')?.trim(); if (!summary) return; await change(`/projects/${project.id}/activities`, 'POST', { project_id: project.id, summary, ...(member ? { notebook_id: notebookId, cell_id: selectedCell } : {}), ...(taskFilter ? { task_id: taskFilter } : {}) }); await loadProject(project.id) })}>Record owner activity</button>
        </section>}
      </>}
    </SidePanel>
    <SidePanel side="right"><h2>Discussion</h2>
      {!project ? <p>Select a project to discuss work.</p> : <>
        <button disabled={busy || pending} onClick={() => void run(async () => { const c = await change<RecordData<Conversation>>('/conversations', 'POST', { project_id: project.id, title: 'Discussion' }); await loadProject(project.id); setConversationId(c.id) })}>{conversations.length ? 'New conversation' : 'Start discussion'}</button>
        {conversations.length > 0 && <select aria-label="Conversation" disabled={busy} value={conversationId} onChange={e => setConversationId(e.target.value)}>{conversations.map(c => <option key={c.id} value={c.id}>{c.data.title} · {c.id.slice(0, 5)}</option>)}</select>}
        {messages.map(m => <article className={`message ${m.data.parent_id ? 'reply' : ''}`} id={`message-${m.id}`} key={m.id}><strong>{m.data.author_role} · {m.data.state}</strong>{m.data.parent_id && <a href={`#message-${m.data.parent_id}`}>In reply to message</a>}<p>{m.data.content}</p><button onClick={() => setReply(m.id)}>Reply to message</button></article>)}
        {conversationId && <form onSubmit={e => { e.preventDefault(); if (!message.trim()) return; void run(async () => { await change(`/conversations/${conversationId}/messages`, 'POST', { content: message, parent_id: reply, state: needsInput ? 'needs_input' : 'published' }); setMessage(''); setReply(null); setMessages(await all<Message>(`/conversations/${conversationId}/messages`)) }) }}>
          {reply && <p>Reply to {reply.slice(0, 8)} <button type="button" onClick={() => setReply(null)}>Cancel reply</button></p>}
          <label>Message<textarea required maxLength={32000} value={message} onChange={e => setMessage(e.target.value)} /></label><label><input type="checkbox" checked={needsInput} onChange={e => setNeedsInput(e.target.checked)} />Needs input</label><button disabled={busy || pending || !message.trim()}>Send</button>
        </form>}
        {member && <><h3>Cell comments</h3><p>Selected cell: {commentCell ?? 'none'}</p><button disabled={busy || pending || !commentCell} onClick={() => void run(async () => { const content = prompt('Comment')?.trim(); if (!content) return; await change(`/notebooks/${notebookId}/comments`, 'POST', { project_id: project.id, notebook_id: notebookId, cell_id: commentCell, content }); await refreshComments() })}>Comment on cell</button></>}
        {comments.filter(c => c.data.project_id === project.id && !c.data.parent_id).map(root => <article className="message" tabIndex={-1} data-comment-cell={root.data.cell_id} key={root.id}>
          <button disabled={root.data.anchor_status === 'orphaned'} onClick={() => jump(notebookId, root.data.cell_id)}>Cell {root.data.cell_id} · <span className={`badge ${root.data.anchor_status}`}>{root.data.anchor_status}</span></button><p>{root.data.content}</p><strong>{root.data.resolved ? 'Resolved' : 'Open'}</strong>
          <button disabled={busy || pending} onClick={() => void run(async () => { await change(`/comments/${root.id}`, 'PATCH', { revision: root.revision, resolved: !root.data.resolved }); await refreshComments() })}>{root.data.resolved ? 'Reopen' : 'Resolve'}</button>
          {comments.filter(c => c.data.thread_id === root.id && c.id !== root.id).map(c => <p className="reply" key={c.id}>{c.data.content}</p>)}
          <button disabled={busy || pending} onClick={() => void run(async () => { const content = prompt('Reply')?.trim(); if (!content) return; await change(`/notebooks/${notebookId}/comments`, 'POST', { project_id: project.id, notebook_id: notebookId, cell_id: root.data.cell_id, parent_id: root.id, content }); await refreshComments() })}>Reply to comment</button>
        </article>)}
      </>}
      <p className="muted">Uploads are not yet available.</p><footer>Coordinator / Coding Agent controls are not implemented.</footer>
    </SidePanel>
    {editing && <TaskEditor initial={editing.task} siblings={editing.roadmap.data.tasks} onClose={() => setEditing(null)} onSave={async task => {
      const r = editing.roadmap
      try {
        if (editing.newTask) await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective: r.data.objective, tasks: [...r.data.tasks.map(rawTask), task] })
        else await change(`/roadmaps/${r.id}/tasks/${task.id}`, 'PUT', { revision: r.revision, task })
        if (project) await loadProject(project.id)
      } finally { setPending(!!pendingMutation()) }
    }} />}
  </div>
}
