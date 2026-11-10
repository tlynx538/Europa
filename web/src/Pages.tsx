import { useCallback, useEffect, useState } from 'react'
import { ApiError, json, request } from './api'
import { all, change, pendingMutation, retryMutation, type Activity, type Message, type Conversation, type Project, type RecordData, type Roadmap } from './applicationApi'
import type { Meta } from './useNotebook'
import type { Navigate } from './Navigation'
import { ActionMenu, ActionItem, ActionSeparator } from './ActionMenu'

type BrowserProps = { navigate: Navigate }
const projectUrl = (id: string) => `/projects/${encodeURIComponent(id)}`
const notebookUrl = (id: string) => `/notebooks/${encodeURIComponent(id)}`
function useOperation() {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [pending, setPending] = useState(!!pendingMutation())
  const run = async (fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true); setError('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false); setPending(!!pendingMutation()) }
  }
  const recovery = pending && <div role="alert" className="alert">An application change has an unknown outcome. Retry the exact operation before changing anything else.
    <button disabled={busy} onClick={() => void run(async () => { await retryMutation(); location.reload() })}>Retry pending change</button></div>
  return { busy, error, pending, run, recovery }
}

export function ProjectsPage({ navigate }: BrowserProps) {
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [query, setQuery] = useState('')
  const { busy, error, pending, run, recovery } = useOperation()
  const [loadError, setLoadError] = useState(''), [loaded, setLoaded] = useState(false)
  useEffect(() => { let live = true; void all<Project>('/projects').then(items => { if (live) { setProjects(items); setLoaded(true) } }).catch(e => { if (live) setLoadError(String(e)) }); return () => { live = false } }, [])
  const visible = projects.filter(p => `${p.data.title} ${p.data.status}`.toLowerCase().includes(query.toLowerCase()))
  return <main className="browser-page"><div className="browser-heading"><h1>Projects</h1></div>{(error || loadError) && <p role="alert" className="alert">{error || loadError}</p>}{recovery}
    <button disabled={busy || pending} onClick={() => void run(async () => {
      const title = prompt('Project title')?.trim(); if (!title) return
      const created = await change<RecordData<Project>>('/projects', 'POST', { title }); await navigate(projectUrl(created.id))
    })}>New project</button>
    <label>Search projects <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
    <ul className="browser-list">{visible.map(p => <li key={p.id}><a href={projectUrl(p.id)} onClick={e => { e.preventDefault(); void navigate(projectUrl(p.id)) }}>{p.data.title}</a><span>{p.data.status} · {p.data.notebooks.filter(n => n.present).length} notebooks</span></li>)}</ul>
    {loaded && visible.length === 0 && <p className="browser-empty">{projects.length ? 'No projects match your search.' : 'No projects yet. Create a project to organize notebooks.'}</p>}
  </main>
}

function LegacyProjectThread({ conversation }: { conversation: RecordData<Conversation> }) {
  const [messages, setMessages] = useState<RecordData<Message>[]>([]), [error, setError] = useState('')
  useEffect(() => { let live = true
    void all<Message>(`/conversations/${conversation.id}/messages`).then(items => { if (live) setMessages(items) }).catch(e => { if (live) setError(String(e)) })
    return () => { live = false }
  }, [conversation.id])
  return <li>{conversation.data.title} · {conversation.id}{conversation.notebook_id ? ` · Notebook ${conversation.notebook_id}` : ' · Project-wide'}
    {error && <p role="alert">{error}</p>}{messages.map(m => <article key={m.id}><strong>{m.data.author_role}</strong> · {m.id}<p>{m.data.content}</p></article>)}</li>
}

export function ProjectDetail({ id, navigate }: BrowserProps & { id: string }) {
  const [project, setProject] = useState<RecordData<Project> | null>(null), [notebooks, setNotebooks] = useState<Meta[]>([])
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([])
  const [conversations, setConversations] = useState<RecordData<Conversation>[]>([]), [activities, setActivities] = useState<RecordData<Activity>[]>([])
  const [errorLoad, setErrorLoad] = useState(''), [missing, setMissing] = useState(false)
  const [query, setQuery] = useState(''), [sort, setSort] = useState('title-asc')
  const { busy, error, pending, run, recovery } = useOperation()
  const refresh = useCallback(async () => {
    const [p, n, projects, roadmaps, conversations, activities] = await Promise.all([
      request<RecordData<Project>>(projectUrl(id)), request<Meta[]>('/notebooks'), all<Project>('/projects'),
      all<Roadmap>(`/roadmaps?project_id=${encodeURIComponent(id)}`), all<Conversation>(`/conversations?project_id=${encodeURIComponent(id)}`), all<Activity>(`${projectUrl(id)}/activities`),
    ])
    setProject(p); setNotebooks(n); setProjects(projects); setRoadmaps(roadmaps); setConversations(conversations); setActivities(activities)
  }, [id])
  useEffect(() => { let live = true; setProject(null); setMissing(false); setErrorLoad('');
    void request<RecordData<Project>>(projectUrl(id)).then(p => {
      if (!live) return
      setProject(p)
      void Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects'), all<Roadmap>(`/roadmaps?project_id=${encodeURIComponent(id)}`), all<Conversation>(`/conversations?project_id=${encodeURIComponent(id)}`), all<Activity>(`${projectUrl(id)}/activities`)]).then(([n, ps, r, c, a]) => {
        if (live) { setNotebooks(n); setProjects(ps); setRoadmaps(r); setConversations(c); setActivities(a) }
      }).catch(e => { if (live) setErrorLoad(String(e)) })
    }).catch(e => { if (live) { if (e instanceof ApiError && e.status === 404) setMissing(true); else setErrorLoad(String(e)) } })
    return () => { live = false }
  }, [id])
  const memberIds = new Set(projects.flatMap(p => p.data.notebooks.map(n => n.notebook_id)))
  const available = notebooks.filter(n => !memberIds.has(n.notebook_id))
  const titleFor = (notebook: Project['notebooks'][number]) => notebooks.find(item => item.notebook_id === notebook.notebook_id)?.title ?? 'Missing notebook'
  const visible = project?.data.notebooks.filter(n => titleFor(n).toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => sort === 'title-desc' ? titleFor(b).localeCompare(titleFor(a)) : titleFor(a).localeCompare(titleFor(b))) ?? []
  return <main className="browser-page"><a href="/" onClick={e => { e.preventDefault(); void navigate('/') }}>← Back to Europa Home</a><nav className="browser-breadcrumb" aria-label="Breadcrumb">Legacy project / {project?.data.title ?? 'Project'}</nav>
    {missing && <h1>Project not found</h1>}
    {errorLoad && <p role="alert" className="alert">{errorLoad}</p>}{error && <p role="alert" className="alert">{error}</p>}{recovery}
     {project && <><div className="browser-heading"><h1>{project.data.title}</h1><ActionMenu label="Project actions" disabled={busy || pending}>
       <ActionItem className="menu-row" disabled={project.data.status !== 'active'} onSelect={() => void run(async () => {
        const title = prompt('Project title', project.data.title)?.trim(); if (!title) return
        await change(projectUrl(id), 'PATCH', { revision: project.revision, title, status: project.data.status }); await refresh()
       })}>Rename project</ActionItem>
       {project.data.status !== 'deleted' && <><ActionSeparator className="menu-separator"/><ActionItem className="menu-row" onSelect={() => void run(async () => {
        const status = project.data.status === 'active' ? 'archived' : 'active'
        if (!confirm(`${status === 'archived' ? 'Archive' : 'Restore'} project ${project.data.title}? Notebook documents are not deleted.`)) return
        await change(projectUrl(id), 'PATCH', { revision: project.revision, status }); await refresh()
       })}>{project.data.status === 'active' ? 'Archive' : 'Restore'}</ActionItem>
       <ActionItem className="menu-row destructive" onSelect={() => void run(async () => {
        if (!confirm(`Delete project ${project.data.title}? This tombstones the project; notebook documents remain.`)) return
        await change(projectUrl(id), 'DELETE', { revision: project.revision }); await refresh()
       })}>Delete project</ActionItem></>}</ActionMenu></div><section id="project-notebooks" aria-label="Notebooks"><h2>Notebooks</h2><p className="muted">{project.data.notebooks.length} notebook{project.data.notebooks.length === 1 ? '' : 's'} · {project.data.status}</p><div className="browser-filters"><label>Search project notebooks <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label><label>Sort project notebooks <select value={sort} onChange={e => setSort(e.target.value)}><option value="title-asc">Title A–Z</option><option value="title-desc">Title Z–A</option></select></label></div><ul className="browser-list">{visible.map(n => <li key={n.notebook_id}>
        {n.present && notebooks.some(item => item.notebook_id === n.notebook_id) ? <a href={notebookUrl(n.notebook_id)} onClick={e => { e.preventDefault(); void navigate(notebookUrl(n.notebook_id)) }}><span className="browser-icon browser-icon-file" aria-hidden="true" />{titleFor(n)}</a> : <span><span className="browser-icon browser-icon-file" aria-hidden="true" />Missing notebook ({n.notebook_id})</span>}
         <ActionMenu label={`Actions for ${notebooks.find(item => item.notebook_id === n.notebook_id)?.title ?? n.notebook_id}`} disabled={busy || pending}><ActionItem className="menu-row destructive" onSelect={() => void run(async () => {
          if (!confirm('Remove this notebook from the project? The notebook document will remain.')) return
          await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id: n.notebook_id, associated: false }); await refresh()
         })}>Remove from project</ActionItem></ActionMenu></li>)}</ul>
      {project.data.status === 'active' && <label>Add notebook <select aria-label="Add notebook" disabled={busy || pending} value="" onChange={e => { const notebook_id = e.target.value; if (!notebook_id) return; void run(async () => {
        await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id }); await refresh()
      }) }}><option value="">Choose a notebook</option>{available.map(n => <option key={n.notebook_id} value={n.notebook_id}>{n.title}</option>)}</select></label>}
      {project.data.status === 'active' && <button disabled={busy || pending} onClick={() => void run(async () => {
        const title = prompt('Notebook title')?.trim(); if (!title) return
        let created: { metadata: { europa: Meta } }
        try { created = await request('/notebooks', json('POST', { title })) }
        catch (e) {
          if (!(e instanceof ApiError) || !e.recoveryNotebookId) throw e
          await navigate(notebookUrl(e.recoveryNotebookId))
          throw new Error(`Notebook ${e.recoveryNotebookId} was saved but its conversation is unavailable. Open the saved notebook and retry the canonical conversation there; do not create it again.`)
        }
        const notebook_id = created.metadata.europa.notebook_id
        try { await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id }) }
        catch (e) { await refresh().catch(() => {}); throw new Error(`Notebook ${notebook_id} was created but could not be assigned. It remains standalone; retry the pending association before any other application change. ${String(e)}`) }
        await refresh(); await navigate(notebookUrl(notebook_id))
      })}>New notebook in project</button>}{!visible.length && <p className="browser-empty">{query ? 'No notebooks match your search.' : 'No notebooks in this project yet. Create a notebook here or add an existing one.'}</p>}</section>
      <details className="project-details"><summary>Project details and history</summary><section id="overview"><h2>Overview</h2><p>Status: {project.data.status}</p><p>{project.data.notebooks.length} notebooks · {roadmaps.length} roadmaps · {conversations.length} conversations · {activities.length} activities</p></section><section id="project-roadmap"><h2>Roadmap</h2>{roadmaps.length ? <ul>{roadmaps.map(r => <li key={r.id}>{r.data.objective} · {r.data.progress}% · {r.data.tasks.length} tasks</li>)}</ul> : <p>No roadmaps yet. Open a notebook to work on a roadmap.</p>}</section>
      <section id="project-conversations"><h2>Legacy project conversation history (read-only)</h2>{conversations.length ? <ul>{conversations.map(c => <LegacyProjectThread key={c.id} conversation={c} />)}</ul> : <p>No legacy project conversations.</p>}</section>
       <section id="project-activity"><h2>Activity</h2>{activities.length ? <ActivityTimeline activities={activities} navigate={navigate} notebooks={notebooks} roadmaps={roadmaps} /> : !errorLoad && <p>No activity recorded for this project. Open a notebook to continue work.</p>}</section></details>
    </>}
  </main>
}

export function NotebookLibrary({ navigate, currentNotebookId, onNotebookDeleted }: BrowserProps & { currentNotebookId?: string; onNotebookDeleted?: (id: string) => void }) {
  const [notebooks, setNotebooks] = useState<Meta[]>([]), [query, setQuery] = useState(''), [loadError, setLoadError] = useState('')
  const [sort, setSort] = useState('title-asc'), [loaded, setLoaded] = useState(false)
  const [projects, setProjects] = useState<RecordData<Project>[]>([])
  const { busy, error, run } = useOperation()
  const refresh = async () => { const [n, p] = await Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects')]); setNotebooks(n); setProjects(p); setLoaded(true) }
  useEffect(() => { let live = true; void Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects')]).then(([n, p]) => { if (live) { setNotebooks(n); setProjects(p); setLoaded(true) } }).catch(e => { if (live) setLoadError(String(e)) }); return () => { live = false } }, [])
  const visible = notebooks.filter(n => n.title.toLowerCase().includes(query.toLowerCase())).sort((a, b) => sort === 'revision-desc' ? b.revision - a.revision || a.title.localeCompare(b.title) : sort === 'title-desc' ? b.title.localeCompare(a.title) : a.title.localeCompare(b.title))
   return <main className="browser-page notebook-browser"><div className="browser-heading"><div><h1>Notebooks</h1><p className="muted">Saved notebooks · {notebooks.length} total</p></div></div>{(error || loadError) && <p role="alert" className="alert">{error || loadError}</p>}
    <button disabled={busy} onClick={() => void run(async () => {
      const title = prompt('Notebook title')?.trim(); if (!title) return
      let created: { metadata: { europa: Meta } }
      try { created = await request('/notebooks', json('POST', { title })) }
      catch (e) {
        if (!(e instanceof ApiError) || !e.recoveryNotebookId) throw e
        await navigate(notebookUrl(e.recoveryNotebookId))
        throw new Error(`Notebook ${e.recoveryNotebookId} was saved but its conversation is unavailable. Open the saved notebook and retry the canonical conversation there; do not create it again.`)
      }
      await navigate(notebookUrl(created.metadata.europa.notebook_id)); await refresh()
    })}>New notebook</button>
    <div className="browser-filters"><label>Search notebooks <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label><label>Sort notebooks <select value={sort} onChange={e => setSort(e.target.value)}><option value="title-asc">Title A–Z</option><option value="title-desc">Title Z–A</option><option value="revision-desc">Revision (highest first)</option></select></label></div>
    <ul className="browser-list">{visible.map(n => <li key={n.notebook_id}>
      <a href={notebookUrl(n.notebook_id)} onClick={e => { e.preventDefault(); void navigate(notebookUrl(n.notebook_id)) }}>{n.title}</a>
      <span>Revision {n.revision}</span>
      {(() => { const p = projects.find(p => p.data.notebooks.some(member => member.notebook_id === n.notebook_id)); return p ? <a href={projectUrl(p.id)} onClick={e => { e.preventDefault(); void navigate(projectUrl(p.id)) }}>{p.data.title}</a> : <span>Standalone</span> })()}
      <ActionMenu label={`Actions for ${n.title}`} disabled={busy}><ActionItem className="menu-row" onSelect={() => void run(async () => {
        const title = prompt('Notebook title', n.title)?.trim(); if (!title) return
        await request(notebookUrl(n.notebook_id), json('PATCH', { revision: n.revision, title })); await refresh()
       })}>Rename</ActionItem><ActionSeparator className="menu-separator" />
       <ActionItem className="menu-row destructive" disabled={!!localStorage.getItem(`europa.drafts.${n.notebook_id}`)} title={localStorage.getItem(`europa.drafts.${n.notebook_id}`) ? 'Save or discard browser drafts in the editor before deleting this notebook.' : undefined} onSelect={() => void run(async () => {
        if (localStorage.getItem(`europa.drafts.${n.notebook_id}`)) throw new Error('Save or discard browser drafts before deleting this notebook.')
        if (!confirm(`Delete notebook ${n.title}? The saved document will be removed; project history remains.${currentNotebookId === n.notebook_id ? ' This also closes your previously selected notebook.' : ''}`)) return
        await request(notebookUrl(n.notebook_id) + `?revision=${n.revision}`, { method: 'DELETE' })
        if (localStorage.getItem('europa.notebook') === n.notebook_id) localStorage.removeItem('europa.notebook')
        if (n.notebook_id === currentNotebookId) {
          if (onNotebookDeleted) onNotebookDeleted(n.notebook_id)
          else { location.reload(); return } // Clear an editor controller still holding the deleted document.
        } else onNotebookDeleted?.(n.notebook_id)
        await refresh()
       })}>Delete</ActionItem></ActionMenu>{localStorage.getItem(`europa.drafts.${n.notebook_id}`) && <small>Save or discard browser drafts before deleting.</small>}</li>)}</ul>
    {loaded && visible.length === 0 && <p className="browser-empty">{notebooks.length ? 'No notebooks match your search.' : query ? 'No notebooks match your search. Create a notebook to get started.' : 'No notebooks yet. Create a notebook to get started.'}</p>}
  </main>
}

export function ActivityPage({ navigate }: BrowserProps) {
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [selected, setSelected] = useState(''), [activities, setActivities] = useState<RecordData<Activity>[]>([]), [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false), [notebooks, setNotebooks] = useState<Meta[]>([]), [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([])
  useEffect(() => { let live = true; void all<Project>('/projects').then(p => { if (live) setProjects(p) }).catch(e => { if (live) setError(String(e)) }); return () => { live = false } }, [])
  useEffect(() => { let live = true; setActivities([]); setLoaded(false); setError(''); if (selected) void Promise.all([all<Activity>(`${projectUrl(selected)}/activities`), request<Meta[]>('/notebooks'), all<Roadmap>(`/roadmaps?project_id=${encodeURIComponent(selected)}`)]).then(([a, n, r]) => { if (live) { setActivities(a); setNotebooks(n); setRoadmaps(r); setLoaded(true) } }).catch(e => { if (live) setError(String(e)) }); return () => { live = false } }, [selected])
  return <main className="browser-page activity-page"><h1>Activity</h1><p className="muted">Owner-recorded project history; execution results remain in notebook receipts.</p>{error && <p role="alert" className="alert">{error}</p>}
    <label>Project <select value={selected} onChange={e => setSelected(e.target.value)}><option value="">Select a project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.data.title}</option>)}</select></label>
     {!selected ? <p>Select a project to see its activity. <a href="/projects" onClick={e => { e.preventDefault(); void navigate('/projects') }}>Browse projects</a></p> : !error && loaded && (activities.length ? <section aria-label="Project activity"><ActivityTimeline activities={activities} notebooks={notebooks} roadmaps={roadmaps} navigate={navigate} /></section> : <p className="browser-empty">No activity recorded for this project. <a href={projectUrl(selected)} onClick={e => { e.preventDefault(); void navigate(projectUrl(selected)) }}>Open project</a> to see its notebooks.</p>)}
   </main>
}

function ActivityTimeline({ activities, notebooks, roadmaps, navigate }: { activities: RecordData<Activity>[]; notebooks: Meta[]; roadmaps: RecordData<Roadmap>[]; navigate: Navigate }) {
  return <ol className="activity-timeline">{[...activities].sort((a, b) => b.created_at - a.created_at).map(a => {
    const notebook = notebooks.find(n => n.notebook_id === a.data.notebook_id)
    const task = roadmaps.flatMap(r => r.data.tasks).find(t => t.id === a.data.task_id)
    const target = a.data.notebook_id ? notebookUrl(a.data.notebook_id) : null
    return <li key={a.id}><div className="activity-event"><strong>{a.data.summary}</strong><span className="badge">{a.data.result}</span></div><time dateTime={new Date(a.created_at * 1000).toISOString()}>{new Date(a.created_at * 1000).toLocaleString()}</time>
      {(target || task || a.data.cell_id) && <div className="activity-links">{target && <a href={target} onClick={e => { e.preventDefault(); void navigate(target) }}>{notebook?.title ?? 'Notebook'}</a>}{a.data.cell_id && <span>Cell reference: {a.data.cell_id}</span>}{task && <span>Task: {task.title}</span>}</div>}</li>
  })}</ol>
}
