import { useCallback, useEffect, useRef, useState } from 'react'
import { HeaderSlot, SidePanel, usePanels } from './Layout'
import { TaskEditor } from './TaskEditor'
import { all, change, pendingMutation, retryMutation, rawTask, type RecordData, type Project, type Roadmap, type Conversation, type Message, type Comment, type Activity, type Task } from './applicationApi'
import { request } from './api'
import type { Navigate } from './Navigation'

type Props = { notebookId?: string; cells: { id: string }[]; selectedCell?: string; showActivity: boolean; revision?: number; navigate: Navigate; onOpenNotebook: (id: string, cell?: string) => Promise<boolean>; onCommentCounts: (counts: Record<string, number>) => void }

export function Workspace({ notebookId, cells, selectedCell, showActivity, revision, navigate, onOpenNotebook, onCommentCounts }: Props) {
  const panels = usePanels()
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [project, setProject] = useState<RecordData<Project> | null>(null)
  const [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([]), [conversations, setConversations] = useState<RecordData<Conversation>[]>([])
  const [messages, setMessages] = useState<RecordData<Message>[]>([]), [comments, setComments] = useState<RecordData<Comment>[]>([])
  const [activities, setActivities] = useState<RecordData<Activity>[]>([])
  const [conversationId, setConversationId] = useState(''), [message, setMessage] = useState(''), [reply, setReply] = useState<string | null>(null), [needsInput, setNeedsInput] = useState(false)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pending, setPending] = useState(!!pendingMutation())
  const [contextNotice, setContextNotice] = useState('')
  const [messagesLoaded, setMessagesLoaded] = useState(false)
  const [editing, setEditing] = useState<{ roadmap: RecordData<Roadmap>; task: Task; newTask: boolean } | null>(null)
  const [taskFilter, setTaskFilter] = useState<string | null>(null)
  const generation = useRef(0), locked = useRef(false)
  const settingsRef = useRef<HTMLDialogElement>(null)
  const settingsOpener = useRef<HTMLElement | null>(null)
  const [navigationNotice, setNavigationNotice] = useState('')
  useEffect(() => {
    const dialog = settingsRef.current
    if (!dialog) return
    if (panels.settingsOpen && !dialog.open) {
      settingsOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      dialog.showModal()
    }
    else if (!panels.settingsOpen && dialog.open) dialog.close()
  }, [panels.settingsOpen])
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
    const reload = () => { if (!document.hidden) void all<Project>('/projects').then(setProjects).catch(e => setError(String(e))) }
    reload()
    window.addEventListener('focus', reload); document.addEventListener('visibilitychange', reload)
    return () => { window.removeEventListener('focus', reload); document.removeEventListener('visibilitychange', reload) }
  }, [])
  useEffect(() => {
    if (!notebookId) return
    const matching = projects.find(p => p.data.notebooks.some(n => n.notebook_id === notebookId && n.present))
    if (matching) { setContextNotice(''); void loadProject(matching.id).catch(e => setError(String(e))) }
    else if (project) {
      setContextNotice(`Project context cleared: ${project.data.title} is not linked to this notebook.`)
      ++generation.current; setProject(null); setRoadmaps([]); setConversations([]); setConversationId(''); setActivities([])
      localStorage.removeItem('europa.project')
    }
  }, [notebookId, projects])
  const loadComments = useCallback(async () => notebookId ? all<Comment>(`/notebooks/${notebookId}/comments`) : [], [notebookId])
  const refreshComments = async () => { const records = await loadComments(); if (currentNotebook.current === notebookId) setComments(records) }
  useEffect(() => { let stale = false; setComments([]); void loadComments().then(c => { if (!stale) setComments(c) }).catch(e => { if (!stale) setError(String(e)) }); return () => { stale = true } }, [loadComments, revision])
  useEffect(() => {
    const counts: Record<string, number> = {}
    for (const c of comments) if (c.data.project_id === project?.id && !c.data.resolved && !c.data.parent_id) counts[c.data.cell_id] = (counts[c.data.cell_id] ?? 0) + 1
    onCommentCounts(counts)
  }, [comments, project?.id, onCommentCounts])
  useEffect(() => {
    let stale = false; setMessages([]); setMessagesLoaded(false); setReply(null)
    if (conversationId) void all<Message>(`/conversations/${conversationId}/messages`).then(m => { if (!stale) { setMessages(m); setMessagesLoaded(true) } }).catch(e => { if (!stale) setError(String(e)) })
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
    try {
      if (project && notebookId && project.data.notebooks.some(n => n.notebook_id === notebookId && n.present)) {
        const latest = await all<Project>('/projects'); setProjects(latest)
        if (!latest.some(p => p.id === project.id && p.data.notebooks.some(n => n.notebook_id === notebookId && n.present))) throw new Error('Project membership changed. Refresh the project context before making changes.')
      }
      await fn()
    } catch (e) { setError(String(e)) } finally { locked.current = false; setBusy(false); setPending(!!pendingMutation()) }
  }
  const member = project?.data.notebooks.some(n => n.notebook_id === notebookId && n.present)
  const blankTask = (): Task => ({ id: crypto.randomUUID(), title: '', acceptance_criteria: [''], parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1,
    links: member ? { notebook_id: notebookId, cell_id: selectedCell ?? null } : {}, evidence: [], accepted: false })
  const commentCell = selectedCell ?? cells[0]?.id
  const cellLabel = (id: string) => { const index = cells.findIndex(c => c.id === id); return index < 0 ? 'Missing cell' : `Cell ${index + 1}` }
  const jump = async (notebook: string | null | undefined, cell?: string | null) => {
    if (!notebook || !await onOpenNotebook(notebook, cell ?? undefined)) return
    if (panels.mobile) { panels.setLeft(false); panels.setRight(false) }
  }
  return <div className="workspace">{contextNotice && <HeaderSlot><small className="context-note">{contextNotice}</small></HeaderSlot>}<SidePanel side="left"><h2>Roadmap</h2>
      {error && <p role="alert" className="alert">{error}</p>}
      {pending && <div className="alert"><p>An operation has an unknown outcome. Retry the identical operation safely; no new operation ID will be created.</p><button disabled={busy} onClick={() => void run(async () => { await retryMutation(); await refresh() })}>Retry pending operation</button></div>}
      {!project && <div className="roadmap-empty"><p className="muted">Link this notebook to a project to plan work.</p><button onClick={panels.openSettings}>Open Project settings</button></div>}
      {project && <>
         {roadmaps.length === 0 && <div className="roadmap-empty"><p className="muted">No roadmap yet. Create a plan with its first task in Project settings.</p><button onClick={panels.openSettings}>Open Project settings</button></div>}
         {roadmaps.map(r => <section className="roadmap" key={r.id}><div className="roadmap-title"><h3>{r.data.objective}</h3><strong>{r.data.progress}%</strong></div><div className="progress-label">{r.data.overall_display?.label ?? 'Overall progress'}</div><progress aria-label="Overall project progress" max="100" value={r.data.progress} />
           <h3>Tasks</h3><ol>{r.data.tasks.map((task, index) => <li className={`task ${task.display.compact ? 'compact' : ''} ${task.status}`} key={task.id}>
             <button onClick={() => { setTaskFilter(task.id); void jump(task.links.notebook_id, task.links.cell_id) }}>{task.display.checkmark ? '✓ ' : ''}{task.title}</button><span className={`task-state ${task.status}`}>{task.status.replace('_', ' ')}</span>
             {task.display.show_progress && task.status === 'in_progress' && <><span className="task-percent">{task.progress}%</span><progress aria-label={`${task.title} progress`} max="100" value={task.progress} /></>}
            {task.explanation && <p>{task.explanation}</p>}
            <details><summary>Task details</summary><ul>{task.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>{task.evidence.map((e, i) => <p key={i}>{e.summary}</p>)}
              <button disabled={busy || pending} onClick={() => setEditing({ roadmap: r, task: rawTask(task), newTask: false })}>Edit task</button>
              <button disabled={busy || pending || index === 0} onClick={() => void run(async () => { const tasks = r.data.tasks.map(rawTask); [tasks[index - 1], tasks[index]] = [tasks[index], tasks[index - 1]]; await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective: r.data.objective, tasks }); await loadProject(project.id) })}>Move task up</button>
              <button disabled={busy || pending} onClick={() => { if (confirm('Remove task from the current plan? Historical versions are retained.')) void run(async () => { await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective: r.data.objective, tasks: r.data.tasks.filter(t => t.id !== task.id).map(rawTask) }); await loadProject(project.id) }) }}>Remove task</button>
            </details>
          </li>)}</ol><div className="roadmap-actions"><button disabled={busy || pending} onClick={() => { setEditing({ roadmap: r, task: blankTask(), newTask: true }) }}>Add task</button>
          <button disabled={busy || pending} onClick={() => void run(async () => { const objective = prompt('Roadmap objective', r.data.objective)?.trim(); if (!objective) return; await change(`/roadmaps/${r.id}`, 'PUT', { revision: r.revision, objective, tasks: r.data.tasks.map(rawTask) }); await loadProject(project.id) })}>Edit objective</button></div>
        </section>)}
      </>}
      {project && <>
        {(showActivity || taskFilter) && <section className="activity-zone"><h3>Contextual activity</h3>{taskFilter && <button onClick={() => setTaskFilter(null)}>Clear task filter</button>}
          {activities.filter(a => taskFilter ? a.data.task_id === taskFilter : a.data.notebook_id === notebookId && (!a.data.cell_id || a.data.cell_id === selectedCell)).map(a => <p key={a.id}><button onClick={() => jump(a.data.notebook_id, a.data.cell_id)}>{a.data.summary}</button> · {a.data.result}</p>)}
          <button disabled={busy || pending} onClick={() => void run(async () => { const summary = prompt('Owner activity summary')?.trim(); if (!summary) return; await change(`/projects/${project.id}/activities`, 'POST', { project_id: project.id, summary, ...(member ? { notebook_id: notebookId, cell_id: selectedCell } : {}), ...(taskFilter ? { task_id: taskFilter } : {}) }); await loadProject(project.id) })}>Record owner activity</button>
        </section>}
      </>}
    </SidePanel>
     <SidePanel side="right"><div className="discussion-header"><h2>Discussion</h2>
       {!project ? <p>Select a project in File → Project settings to discuss work.</p> : <>
        <button disabled={busy || pending} onClick={() => void run(async () => { const c = await change<RecordData<Conversation>>('/conversations', 'POST', { project_id: project.id, title: 'Discussion' }); await loadProject(project.id); setConversationId(c.id) })}>{conversations.length ? 'New conversation' : 'Start discussion'}</button>
         {conversations.length > 0 && <select aria-label="Conversation" disabled={busy} value={conversationId} onChange={e => setConversationId(e.target.value)}>{conversations.map(c => <option key={c.id} value={c.id}>{c.data.title} · {c.id.slice(0, 5)}</option>)}</select>}
       </>}</div><div className="discussion-history">
       {project && <>
         {conversationId && messagesLoaded && !error && messages.length === 0 && <p className="muted">No messages yet. Start the conversation below.</p>}
         {messages.map(m => <article className={`message ${m.data.parent_id ? 'reply' : ''}`} id={`message-${m.id}`} key={m.id}><div className="message-meta"><strong>{m.data.author_role}{m.data.state === 'needs_input' ? ' · Needs input' : ''}</strong><time dateTime={new Date(m.created_at * 1000).toISOString()}>{new Date(m.created_at * 1000).toLocaleString()}</time></div>{m.data.parent_id && <a href={`#message-${m.data.parent_id}`}>In reply to message</a>}<p>{m.data.content}</p>{m.data.notebook_id && <button onClick={() => void jump(m.data.notebook_id, m.data.cell_id)}>{m.data.cell_id ? (m.data.notebook_id === notebookId ? cellLabel(m.data.cell_id) : 'Linked cell') : 'Open linked notebook'}</button>}<button onClick={() => setReply(m.id)}>Reply to message</button></article>)}
         {member && <div className="comment-heading"><h3>Cell comments</h3><span className="muted">{commentCell ? cellLabel(commentCell) : 'No cell selected'}</span><button disabled={busy || pending || !commentCell} onClick={() => void run(async () => { const content = prompt('Comment')?.trim(); if (!content) return; await change(`/notebooks/${notebookId}/comments`, 'POST', { project_id: project.id, notebook_id: notebookId, cell_id: commentCell, content }); await refreshComments() })}>Comment on cell</button></div>}
        {comments.filter(c => c.data.project_id === project.id && !c.data.parent_id).map(root => <article className="message" tabIndex={-1} data-comment-cell={root.data.cell_id} key={root.id}>
           <button disabled={root.data.anchor_status === 'orphaned'} onClick={() => void jump(notebookId, root.data.cell_id)}>{cellLabel(root.data.cell_id)} · <span className={`badge ${root.data.anchor_status}`}>{root.data.anchor_status === 'orphaned' ? 'Missing cell' : root.data.anchor_status}</span></button><p>{root.data.content}</p><strong>{root.data.resolved ? 'Resolved' : 'Open'}</strong>
          <button disabled={busy || pending} onClick={() => void run(async () => { await change(`/comments/${root.id}`, 'PATCH', { revision: root.revision, resolved: !root.data.resolved }); await refreshComments() })}>{root.data.resolved ? 'Reopen' : 'Resolve'}</button>
          {comments.filter(c => c.data.thread_id === root.id && c.id !== root.id).map(c => <p className="reply" key={c.id}>{c.data.content}</p>)}
          <button disabled={busy || pending} onClick={() => void run(async () => { const content = prompt('Reply')?.trim(); if (!content) return; await change(`/notebooks/${notebookId}/comments`, 'POST', { project_id: project.id, notebook_id: notebookId, cell_id: root.data.cell_id, parent_id: root.id, content }); await refreshComments() })}>Reply to comment</button>
        </article>)}
       </>}
       </div>
      {conversationId && <form className="message-composer" onSubmit={e => { e.preventDefault(); if (!message.trim()) return; void run(async () => { await change(`/conversations/${conversationId}/messages`, 'POST', { content: message, parent_id: reply, state: needsInput ? 'needs_input' : 'published' }); setMessage(''); setReply(null); setMessages(await all<Message>(`/conversations/${conversationId}/messages`)) }) }}>
        {reply && <p>Reply to {reply.slice(0, 8)} <button type="button" onClick={() => setReply(null)}>Cancel reply</button></p>}
        <label>Message<textarea required maxLength={32000} value={message} onChange={e => setMessage(e.target.value)} /></label><label><input type="checkbox" checked={needsInput} onChange={e => setNeedsInput(e.target.checked)} />Needs input</label><button disabled={busy || pending || !message.trim()}>Send</button>
      </form>}
    </SidePanel>
    <dialog ref={settingsRef} className="project-settings-dialog" aria-labelledby="project-settings-heading" onKeyDown={event => {
      if (event.key !== 'Tab') return
      const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href]')).filter(node => node.getClientRects().length)
      if (!focusable.length) return
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1)?.focus() }
      else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus() }
    }} onClose={() => { panels.closeSettings(); setNavigationNotice(''); requestAnimationFrame(() => {
      const opener = settingsOpener.current
      settingsOpener.current = null
      const visible = opener?.isConnected && opener.getClientRects().length > 0 && getComputedStyle(opener).visibility !== 'hidden' && !opener.closest('[inert], dialog:not([open])')
      if (visible) opener.focus()
      else Array.from(document.querySelectorAll<HTMLElement>('.notebook-menubar [aria-haspopup=menu]')).find(element => element.textContent === 'File')?.focus()
    }) }} onClick={event => {
      if (event.target !== event.currentTarget) return
      const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect()
      if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) panels.closeSettings()
    }}>
      <div className="settings-heading"><div><p className="settings-eyebrow">Workspace / Project</p><h2 id="project-settings-heading">Project settings</h2></div><button type="button" aria-label="Close Project settings" onClick={panels.closeSettings}>Close</button></div>
      {error && <p role="alert" className="alert">{error}</p>}
      {pending && <p className="alert">An operation has an unknown outcome. Retry the identical operation in Roadmap before making changes.</p>}
      <section aria-labelledby="settings-general"><h3 id="settings-general">General / Project context</h3>
        <label>Project context <select aria-label="Project" value={project?.id ?? ''} disabled={busy} onChange={e => { setContextNotice(''); setNavigationNotice(''); if (e.target.value) void run(() => loadProject(e.target.value)); else { ++generation.current; setProject(null); setRoadmaps([]); setConversations([]); setConversationId(''); setActivities([]); localStorage.removeItem('europa.project') } }}><option value="">Select project</option>{projects.map(p => <option value={p.id} key={p.id}>{p.data.title}</option>)}</select></label>
        {project ? <><p><strong>{project.data.title}</strong> · {project.data.status} · Revision {project.revision}</p><button disabled={busy || pending} onClick={() => void (async () => { setNavigationNotice(''); try { if (await navigate(`/projects/${project.id}`)) panels.closeSettings(); else setNavigationNotice('Could not leave this notebook. Resolve the pending operation or confirm keeping drafts, then try again.') } catch (e) { setNavigationNotice(String(e)) } })()}>Manage project (rename, archive, notebooks)</button>{navigationNotice && <p role="status" className="alert">{navigationNotice}</p>}</> : <p className="muted">No project selected. Choose an existing project to plan work.</p>}
      </section>
      <section aria-labelledby="settings-notebooks"><h3 id="settings-notebooks">Notebooks</h3><p>{project ? `${project.data.notebooks.filter(n => n.present).length} linked notebook${project.data.notebooks.filter(n => n.present).length === 1 ? '' : 's'}` : 'Select a project to see notebook membership.'}</p>{project && <p className="muted">{member ? 'Current notebook is linked' : 'Current notebook is not linked'} · Manage membership in the project view.</p>}</section>
      <section aria-labelledby="settings-roadmap"><h3 id="settings-roadmap">Roadmap / planning</h3><p>{!project ? 'Select a project to create a roadmap.' : roadmaps.length ? `${roadmaps.length} roadmap${roadmaps.length === 1 ? '' : 's'} available. Add tasks and edit objectives in Roadmap.` : 'No roadmap yet. Create a plan with its first task.'}</p>
        {project && roadmaps.length === 0 && <button disabled={busy || pending} onClick={() => void run(async () => {
          const objective = prompt('Roadmap objective')?.trim(); if (!objective) return
          const title = prompt('First task title')?.trim(); if (!title) return
          const criterion = prompt('Acceptance criterion')?.trim(); if (!criterion) return
          await change('/roadmaps', 'POST', { project_id: project.id, ...(member ? { notebook_id: notebookId } : {}), objective, tasks: [{ ...blankTask(), title, acceptance_criteria: [criterion] }] }); await loadProject(project.id)
        })}>Create roadmap</button>}
      </section>
      <section aria-labelledby="settings-history"><h3 id="settings-history">History</h3><p>Reload project, roadmap, discussion and notebook comment history.</p><button disabled={busy || !project} onClick={() => void run(refresh)}>Refresh project history</button></section>
    </dialog>
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
