import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { HeaderSlot, SidePanel, usePanels } from './Layout'
import { TaskEditor } from './TaskEditor'
import { all, change, pendingMutation, retryMutation, rawTask, type RecordData, type Project, type Roadmap, type Conversation, type Message, type Comment, type Activity, type Task } from './applicationApi'
import { request } from './api'
import type { Navigate } from './Navigation'
import { projectConversation } from './conversationProjection'
import { ConversationComposer, clearSentDraft, type CellReference } from './ConversationComposer'
import { InlineCommentComposer, clearSentCommentDraft } from './InlineCommentComposer'

type Canvas = { beforeCells: ReactNode; afterCell: (id: string) => ReactNode; history: ReactNode; composer: ReactNode; discussCell: (id: string) => void }
type Props = { notebookId?: string; cells: { id: string; source?: string }[]; draftCells?: string[]; conflict?: boolean; selectedCell?: string; showActivity: boolean; revision?: number; navigate: Navigate; onOpenNotebook: (id: string, cell?: string) => Promise<boolean>; onCommentCounts: (counts: Record<string, number>) => void; renderNotebook?: (view: Canvas) => ReactNode }

export function Workspace({ notebookId, cells, draftCells = [], conflict = false, selectedCell, showActivity, revision, navigate, onOpenNotebook, onCommentCounts, renderNotebook }: Props) {
  const panels = usePanels()
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [project, setProject] = useState<RecordData<Project> | null>(null)
  const [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([])
  const [canonicalConversation, setConversation] = useState<RecordData<Conversation> | null>(null)
  const conversation = canonicalConversation?.notebook_id === notebookId ? canonicalConversation : null
  const [canonicalError, setCanonicalError] = useState('')
  const [legacy, setLegacy] = useState<{ conversation: RecordData<Conversation>; messages: RecordData<Message>[] }[]>([])
  const [messages, setMessages] = useState<RecordData<Message>[]>([]), [comments, setComments] = useState<RecordData<Comment>[]>([])
  const [activities, setActivities] = useState<RecordData<Activity>[]>([])
  const [reply, setReply] = useState<string | null>(null), [reference, setReference] = useState<(CellReference & { notebook: string; conversation: string }) | null>(null)
  const [activeComment, setActiveComment] = useState<{ notebook: string; project: string; cell: string; parent: string | null } | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pending, setPending] = useState(!!pendingMutation())
  const [contextNotice, setContextNotice] = useState('')
  const [messagesLoaded, setMessagesLoaded] = useState(false)
  const [editing, setEditing] = useState<{ roadmap: RecordData<Roadmap>; task: Task; newTask: boolean } | null>(null)
  const [taskFilter, setTaskFilter] = useState<string | null>(null)
  const generation = useRef(0), locked = useRef(false)
  const composerShell = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const shell = composerShell.current
    const column = shell?.closest<HTMLElement>('.notebook-column')
    if (!shell || !column) return
    const measure = () => column.style.setProperty('--composer-reserve', `${shell.getBoundingClientRect().height}px`)
    const observer = new ResizeObserver(measure)
    observer.observe(shell)
    measure()
    return () => { observer.disconnect(); column.style.removeProperty('--composer-reserve') }
  }, [notebookId])
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
  const loadProject = useCallback(async (id: string) => {
    const token = ++generation.current
    const [p, r, a] = await Promise.all([request<RecordData<Project>>(`/projects/${id}`), all<Roadmap>(`/roadmaps?project_id=${id}`), all<Activity>(`/projects/${id}/activities`)])
    if (generation.current !== token) return
    setProject(p); setRoadmaps(r); setActivities(a)
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
      ++generation.current; setProject(null); setRoadmaps([]); setActivities([])
      localStorage.removeItem('europa.project')
    }
  }, [notebookId, projects])
  useEffect(() => {
    let stale = false; setLegacy([])
    if (notebookId) void all<Conversation>(`/notebooks/${encodeURIComponent(notebookId)}/legacy-conversations`).then(async threads => {
      const history = await Promise.all(threads.filter(c => c.notebook_id === notebookId).map(async conversation => ({ conversation, messages: await all<Message>(`/conversations/${conversation.id}/messages`) })))
      if (!stale) setLegacy(history)
    }).catch(e => { if (!stale) setError(String(e)) })
    return () => { stale = true }
  }, [notebookId])
  const loadComments = useCallback(async () => notebookId ? all<Comment>(`/notebooks/${notebookId}/comments`) : [], [notebookId])
  const refreshComments = async () => { const records = await loadComments(); if (notebookId) setComments(records) }
  useEffect(() => { let stale = false; setComments([]); void loadComments().then(c => { if (!stale) setComments(c) }).catch(e => { if (!stale) setError(String(e)) }); return () => { stale = true } }, [loadComments, revision])
  useEffect(() => {
    const counts: Record<string, number> = {}
    for (const c of comments) if (c.data.project_id === project?.id && !c.data.resolved && !c.data.parent_id) counts[c.data.cell_id] = (counts[c.data.cell_id] ?? 0) + 1
    onCommentCounts(counts)
  }, [comments, project?.id, onCommentCounts])
  const loadCanonical = useCallback(async (id: string) => {
    const record = await request<RecordData<Conversation>>(`/notebooks/${encodeURIComponent(id)}/conversation`)
    if (record.notebook_id !== id) throw new Error('Notebook conversation scope mismatch')
    return record
  }, [])
  useEffect(() => {
    let stale = false
    setConversation(null); setMessages([]); setMessagesLoaded(false); setCanonicalError(''); setReply(null); setReference(null)
    if (notebookId) void loadCanonical(notebookId).then(c => { if (!stale) setConversation(c) }).catch(e => { if (!stale) setCanonicalError(String(e)) })
    return () => { stale = true }
  }, [notebookId, loadCanonical])
  useEffect(() => {
    let stale = false; setMessages([]); setMessagesLoaded(false)
    if (conversation) void all<Message>(`/conversations/${conversation.id}/messages`).then(m => { if (!stale) { setMessages(m); setMessagesLoaded(true) } }).catch(e => { if (!stale) setCanonicalError(String(e)) })
    return () => { stale = true }
  }, [conversation])
  const retryCanonical = () => {
    if (!notebookId || busy) return
    setCanonicalError(''); setMessagesLoaded(false)
    void loadCanonical(notebookId).then(c => { if (c.id === conversation?.id) {
      void all<Message>(`/conversations/${c.id}/messages`).then(m => { setMessages(m); setMessagesLoaded(true) }).catch(e => setCanonicalError(String(e)))
    } else setConversation(c) }).catch(e => setCanonicalError(String(e)))
  }
  async function refresh() {
    setProjects(await all<Project>('/projects'))
    if (project) await loadProject(project.id)
    await refreshComments()
    if (conversation) { setMessages(await all<Message>(`/conversations/${conversation.id}/messages`)); setMessagesLoaded(true) }
  }
  async function run(fn: () => Promise<void>, projectMutation = true) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError('')
    try {
      if (projectMutation && project && notebookId && project.data.notebooks.some(n => n.notebook_id === notebookId && n.present)) {
        const latest = await all<Project>('/projects'); setProjects(latest)
        if (!latest.some(p => p.id === project.id && p.data.notebooks.some(n => n.notebook_id === notebookId && n.present))) throw new Error('Project membership changed. Refresh the project context before making changes.')
      }
      await fn()
    } catch (e) { setError(String(e)) } finally { locked.current = false; setBusy(false); setPending(!!pendingMutation()) }
  }
  const member = project?.data.notebooks.some(n => n.notebook_id === notebookId && n.present)
  const blankTask = (): Task => ({ id: crypto.randomUUID(), title: '', acceptance_criteria: [''], parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1,
    links: member ? { notebook_id: notebookId, cell_id: selectedCell ?? null } : {}, evidence: [], accepted: false })
  const cellLabel = (id: string) => { const index = cells.findIndex(c => c.id === id); return index < 0 ? 'Missing cell' : `Cell ${index + 1}` }
  const openComment = (cell: string, parent: string | null) => {
    if (project && notebookId && member) setActiveComment({ notebook: notebookId, project: project.id, cell, parent })
  }
  const commentForm = (cell: string, parent: string | null) => {
    if (!activeComment || activeComment.notebook !== notebookId || activeComment.project !== project?.id || activeComment.cell !== cell || activeComment.parent !== parent) return null
    const attached = cells.find(c => c.id === cell)
    return <InlineCommentComposer key={`${notebookId}.${project.id}.${cell}.${parent}`} notebook={notebookId} project={project.id} cell={cell} parent={parent} cellLabel={cellLabel(cell)} source={attached?.source ?? ''} revision={revision}
      invalid={!member || (parent !== null ? !comments.some(c => c.id === parent && c.notebook_id === notebookId && c.data.cell_id === cell && c.data.project_id === project.id && !c.data.parent_id) : !attached || draftCells.includes(cell) || conflict)}
      busy={busy} pending={pending} onCancel={() => setActiveComment(null)} onSend={async (content, reviewedRevision, stillCurrent) => {
        let sent = false
        await run(async () => {
          await change(`/notebooks/${notebookId}/comments`, 'POST', { project_id: project.id, notebook_id: notebookId, cell_id: cell, ...(parent ? { parent_id: parent } : { document_revision: reviewedRevision }), content })
          sent = true
          await refreshComments()
        })
        if (sent && stillCurrent()) setActiveComment(null)
        return sent
      }} />
  }
  const jump = async (notebook: string | null | undefined, cell?: string | null) => {
    if (!notebook || !await onOpenNotebook(notebook, cell ?? undefined)) return
    if (panels.mobile) { panels.setLeft(false); panels.setRight(false) }
  }
  const retryPending = async () => {
    const operation = pendingMutation()
    const linkedNotebook = (operation?.body as { notebook_id?: string } | undefined)?.notebook_id
    const linkedProject = (operation?.body as { project_id?: string } | undefined)?.project_id
    const messageRetry = operation?.method === 'POST' && operation.path === `/conversations/${conversation?.id}/messages`
    if (messageRetry ? (!notebookId || (linkedNotebook && linkedNotebook !== notebookId)) : (!project || !member || (linkedNotebook && linkedNotebook !== notebookId) || (linkedProject && linkedProject !== project.id))) throw new Error('Return to the original notebook and context before retrying this operation.')
    await retryMutation()
    if (operation?.method === 'POST' && operation.path.startsWith('/conversations/') && operation.path.endsWith('/messages') && typeof (operation.body as { content?: unknown }).content === 'string') clearSentDraft((operation.body as { notebook_id?: string }).notebook_id ?? notebookId ?? '', operation.path.split('/')[2], (operation.body as { content: string }).content)
    if (operation?.method === 'POST' && operation.path === `/notebooks/${notebookId}/comments`) {
      const body = operation.body as { notebook_id?: string; project_id?: string; cell_id?: string; parent_id?: string | null; content?: string }
      if (body.notebook_id === notebookId && body.project_id && body.cell_id && typeof body.content === 'string') clearSentCommentDraft(notebookId!, body.project_id, body.cell_id, body.parent_id ?? null, body.content)
    }
    await refresh()
  }
  const projection = projectConversation(notebookId ?? '', project?.id ?? '', cells.map(c => c.id), conversation ?? undefined, messages, member ? comments : [])
  const messageView = (m: RecordData<Message>, scope?: string) => <article data-author={m.data.author_role} className={`message ${m.data.parent_id ? 'reply' : ''}`} id={`message-${m.id}`} key={m.id}><div className="message-meta"><strong>{m.data.author_role}{m.data.state === 'needs_input' ? ' · Needs input' : ''}</strong><time dateTime={new Date(m.created_at * 1000).toISOString()} title={new Date(m.created_at * 1000).toLocaleString()}>{new Date(m.created_at * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</time></div>{scope && <small>{scope}</small>}{m.data.cell_id && <small>{cellLabel(m.data.cell_id)} reference{!cells.some(c => c.id === m.data.cell_id) ? ' · historical missing cell' : ''}</small>}{m.data.parent_id && <a href={`#message-${m.data.parent_id}`}>In reply to message</a>}<p>{m.data.content}</p><button onClick={() => setReply(m.id)}>Reply to message</button></article>
  const commentMeta = (record: RecordData<Comment>) => <div className="message-meta"><strong>{record.data.author_role}</strong><time dateTime={new Date(record.created_at * 1000).toISOString()} title={new Date(record.created_at * 1000).toLocaleString()}>{new Date(record.created_at * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</time></div>
  const commentView = (root: RecordData<Comment>) => <article className="message cell-comment" tabIndex={-1} data-comment-cell={root.data.cell_id} key={root.id}>
    <div className="comment-anchor"><button disabled={root.data.anchor_status === 'orphaned' || !cells.some(c => c.id === root.data.cell_id)} onClick={() => void jump(notebookId, root.data.cell_id)}>{cellLabel(root.data.cell_id)} · <span className={`badge ${root.data.anchor_status}`}>{root.data.anchor_status === 'orphaned' ? 'Missing cell' : root.data.anchor_status}</span></button>{commentMeta(root)}</div><p>{root.data.content}</p>
    <div className="comment-actions"><strong>{root.data.resolved ? 'Resolved' : 'Open'}</strong><button disabled={busy || pending} onClick={() => void run(async () => { await change(`/comments/${root.id}`, 'PATCH', { revision: root.revision, resolved: !root.data.resolved }); await refreshComments() })}>{root.data.resolved ? 'Reopen' : 'Resolve'}</button><button disabled={busy || pending || !member} onClick={() => openComment(root.data.cell_id, root.id)}>Reply to comment</button></div>
    {comments.filter(c => c.data.thread_id === root.id && c.id !== root.id).map(c => <div className="comment-reply" key={c.id}>{commentMeta(c)}<p>{c.data.content}</p></div>)}
    {commentForm(root.data.cell_id, root.id)}
  </article>
  const canvas: Canvas = {
    beforeCells: <div className="stream-messages">{projection.beforeCells.map(m => messageView(m))}</div>,
    afterCell: id => <div className="cell-conversation" key={`discussion-${id}`}>{projection.cells[id]?.messages.map(m => messageView(m))}{projection.cells[id]?.comments.map(commentView)}{member && selectedCell === id && <button className="comment-cell-action" disabled={busy || pending} onClick={() => openComment(id, null)}>Comment on cell</button>}{commentForm(id, null)}</div>,
    discussCell: id => { const cell = cells.find(c => c.id === id); if (cell && revision !== undefined && notebookId && conversation) { setReference({ id, source: cell.source ?? '', revision, notebook: notebookId, conversation: conversation.id }); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('.message-composer textarea')?.focus()) } },
    history: <section className="canvas-conversation" aria-label="Conversation notices">
      {!conversation && !canonicalError && <p role="status">Loading conversation…</p>}
      {canonicalError && <div role="alert" className="alert">{canonicalError} <button onClick={retryCanonical}>Retry conversation</button></div>}
      {conversation && !messagesLoaded && !canonicalError && <p role="status">Loading messages…</p>}
      {projection.general.map(m => messageView(m))}
      {projection.orphanMessages.map(m => messageView(m, 'Missing cell reference · historical message'))}
      {projection.orphanPlacement.map(m => messageView(m, 'Missing placement cell · historical message'))}
      {projection.orphanComments.length > 0 && <section aria-label="Comments on missing cells"><h3>Comments on missing cells</h3>{projection.orphanComments.map(commentView)}</section>}
      {legacy.length > 0 && <section className="legacy-history" aria-label="Historical notebook conversations"><h3>Historical notebook conversations · read-only</h3>{legacy.map(({ conversation: thread, messages: history }) => <div key={thread.id}><h4>{thread.data.title} · {thread.id}</h4>{history.filter(m => (!m.notebook_id || m.notebook_id === notebookId) && (!m.data.notebook_id || m.data.notebook_id === notebookId)).map(m => <article className="message" data-author={m.data.author_role} key={m.id}><strong>{m.data.author_role}</strong><small> · {m.id}</small>{m.data.cell_id && <small> · {cellLabel(m.data.cell_id)}{!cells.some(c => c.id === m.data.cell_id) ? ' · Missing cell reference' : ''}</small>}<p>{m.data.content}</p></article>)}</div>)}</section>}

      {error && <p role="alert" className="alert">{error}</p>}{pending && <div className="alert"><p>An operation has an unknown outcome. Retry the identical operation safely; no new operation ID will be created.</p><button disabled={busy} onClick={() => void run(retryPending, pendingMutation()?.path !== `/conversations/${conversation?.id}/messages`)}>Retry pending operation</button></div>}
    </section>,
    composer: notebookId ? <div className="composer-shell" ref={composerShell}>{!conversation && <div className="composer-status" role={canonicalError ? 'alert' : 'status'}>{canonicalError ? <>Conversation unavailable. <button onClick={retryCanonical}>Retry conversation</button></> : 'Loading conversation…'}</div>}
      {conversation && <ConversationComposer key={`${notebookId}.${conversation.id}`} notebookId={notebookId} conversationId={conversation.id} cells={cells.map(c => ({ id: c.id, source: c.source ?? '' }))} drafts={draftCells} conflict={conflict} revision={revision} selectedCell={selectedCell} reference={reference?.notebook === notebookId && reference.conversation === conversation.id ? reference : null} reply={reply} onReplyConsumed={() => setReply(null)} onReferenceRemoved={() => setReference(null)} busy={busy || !messagesLoaded || !!canonicalError} pending={pending} onSend={async (body, content) => { let sent = false; await run(async () => { await change(`/conversations/${conversation.id}/messages`, 'POST', body); sent = true; clearSentDraft(notebookId, conversation.id, content); setMessages(await all<Message>(`/conversations/${conversation.id}/messages`)) }, false); return sent }} />}</div> : null,

  }
  return <div className="workspace">{renderNotebook?.(canvas)}{contextNotice && <HeaderSlot><small className="context-note">{contextNotice}</small></HeaderSlot>}<SidePanel side="left"><h2>Roadmap</h2>
      {error && <p role="alert" className="alert">{error}</p>}
      {pending && <div className="alert"><p>An operation has an unknown outcome. Retry the identical operation safely; no new operation ID will be created.</p><button disabled={busy} onClick={() => void run(retryPending, pendingMutation()?.path !== `/conversations/${conversation?.id}/messages`)}>Retry pending operation</button></div>}
      {!project && <div className="roadmap-empty"><p className="muted">Link this notebook to a project to plan work.</p><button onClick={panels.openSettings}>Open Project settings</button></div>}
      {project && <>
         {roadmaps.length === 0 && <div className="roadmap-empty"><p className="muted">No roadmap yet. Create a plan with its first task in Project settings.</p><button onClick={panels.openSettings}>Open Project settings</button></div>}
         {roadmaps.map(r => <section className="roadmap" key={r.id}><div className="roadmap-title"><h3>{r.data.objective}</h3><strong>{r.data.progress}%</strong></div><div className="progress-label">{r.data.overall_display?.label ?? 'Overall progress'}</div><progress aria-label="Overall project progress" max="100" value={r.data.progress} />
           <h3>Tasks</h3><ol>{r.data.tasks.map((task, index) => <li className={`task ${task.display.compact ? 'compact' : ''} ${task.status}`} key={task.id}>
             <button onClick={() => { setTaskFilter(task.id); void jump(task.links.notebook_id, task.links.cell_id) }}>{task.display.checkmark ? '✓ ' : ''}{task.title}</button><span className={`task-state ${task.status}`}>{task.status === 'pending' ? 'upcoming' : task.status === 'in_progress' ? 'current' : task.status.replace('_', ' ')}</span>
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
        <label>Project context <select aria-label="Project" value={project?.id ?? ''} disabled={busy} onChange={e => { setContextNotice(''); setNavigationNotice(''); if (e.target.value) void run(() => loadProject(e.target.value)); else { ++generation.current; setProject(null); setRoadmaps([]); setActivities([]); localStorage.removeItem('europa.project') } }}><option value="">Select project</option>{projects.map(p => <option value={p.id} key={p.id}>{p.data.title}</option>)}</select></label>
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
