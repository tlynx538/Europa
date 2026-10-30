import { useCallback, useEffect, useState } from 'react'
import { ApiError, json, request } from './api'
import { all, change, pendingMutation, retryMutation, type Activity, type Conversation, type Project, type RecordData, type Roadmap } from './applicationApi'
import type { Meta } from './useNotebook'
import type { Navigate } from './Navigation'

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
  const [loadError, setLoadError] = useState('')
  useEffect(() => { let live = true; void all<Project>('/projects').then(items => { if (live) setProjects(items) }).catch(e => { if (live) setLoadError(String(e)) }); return () => { live = false } }, [])
  const visible = projects.filter(p => `${p.data.title} ${p.data.status}`.toLowerCase().includes(query.toLowerCase()))
  return <main className="browser-page"><h1>Projects</h1>{(error || loadError) && <p role="alert" className="alert">{error || loadError}</p>}{recovery}
    <button disabled={busy || pending} onClick={() => void run(async () => {
      const title = prompt('Project title')?.trim(); if (!title) return
      const created = await change<RecordData<Project>>('/projects', 'POST', { title }); await navigate(projectUrl(created.id))
    })}>New project</button>
    <label>Search projects <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
    <ul className="browser-list">{visible.map(p => <li key={p.id}><a href={projectUrl(p.id)} onClick={e => { e.preventDefault(); void navigate(projectUrl(p.id)) }}>{p.data.title}</a><span>{p.data.status} · {p.data.notebooks.filter(n => n.present).length} notebooks</span></li>)}</ul>
    {visible.length === 0 && <p>No matching projects.</p>}
  </main>
}

export function ProjectDetail({ id, navigate }: BrowserProps & { id: string }) {
  const [project, setProject] = useState<RecordData<Project> | null>(null), [notebooks, setNotebooks] = useState<Meta[]>([])
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [roadmaps, setRoadmaps] = useState<RecordData<Roadmap>[]>([])
  const [conversations, setConversations] = useState<RecordData<Conversation>[]>([]), [activities, setActivities] = useState<RecordData<Activity>[]>([])
  const [errorLoad, setErrorLoad] = useState(''), [missing, setMissing] = useState(false)
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
  return <main className="browser-page"><button onClick={() => void navigate('/projects')}>← Projects</button>
    {missing && <h1>Project not found</h1>}
    {errorLoad && <p role="alert" className="alert">{errorLoad}</p>}{error && <p role="alert" className="alert">{error}</p>}{recovery}
    {project && <><h1>{project.data.title}</h1><section id="overview"><h2>Overview</h2><p>Status: {project.data.status}</p><p>{project.data.notebooks.length} notebooks · {roadmaps.length} roadmaps · {conversations.length} conversations · {activities.length} activities</p></section>
      <button disabled={busy || pending || project.data.status !== 'active'} onClick={() => void run(async () => {
        const title = prompt('Project title', project.data.title)?.trim(); if (!title) return
        await change(projectUrl(id), 'PATCH', { revision: project.revision, title, status: project.data.status }); await refresh()
      })}>Rename project</button>
      {project.data.status !== 'deleted' && <button disabled={busy || pending} onClick={() => void run(async () => {
        const status = project.data.status === 'active' ? 'archived' : 'active'
        if (!confirm(`${status === 'archived' ? 'Archive' : 'Restore'} project ${project.data.title}? Notebook documents are not deleted.`)) return
        await change(projectUrl(id), 'PATCH', { revision: project.revision, status }); await refresh()
      })}>{project.data.status === 'active' ? 'Archive' : 'Restore'}</button>}
      {project.data.status !== 'deleted' && <button disabled={busy || pending} onClick={() => void run(async () => {
        if (!confirm(`Delete project ${project.data.title}? This tombstones the project; notebook documents remain.`)) return
        await change(projectUrl(id), 'DELETE', { revision: project.revision }); await refresh()
      })}>Delete project</button>}
      <section id="project-notebooks"><h2>Notebooks</h2><ul className="browser-list">{project.data.notebooks.map(n => <li key={n.notebook_id}>
        {n.present ? <a href={notebookUrl(n.notebook_id)} onClick={e => { e.preventDefault(); void navigate(notebookUrl(n.notebook_id)) }}>{notebooks.find(item => item.notebook_id === n.notebook_id)?.title ?? n.notebook_id}</a> : <span>Missing notebook {n.notebook_id}</span>}
        <button disabled={busy || pending} onClick={() => void run(async () => {
          if (!confirm('Remove this notebook from the project? The notebook document will remain.')) return
          await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id: n.notebook_id, associated: false }); await refresh()
        })}>Remove from project</button></li>)}</ul>
      {project.data.status === 'active' && <label>Add notebook <select aria-label="Add notebook" disabled={busy || pending} value="" onChange={e => { const notebook_id = e.target.value; if (!notebook_id) return; void run(async () => {
        await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id }); await refresh()
      }) }}><option value="">Choose a notebook</option>{available.map(n => <option key={n.notebook_id} value={n.notebook_id}>{n.title}</option>)}</select></label>}
      {project.data.status === 'active' && <button disabled={busy || pending} onClick={() => void run(async () => {
        const title = prompt('Notebook title')?.trim(); if (!title) return
        const created = await request<{ metadata: { europa: Meta } }>('/notebooks', json('POST', { title }))
        const notebook_id = created.metadata.europa.notebook_id
        try { await change(`${projectUrl(id)}/notebooks`, 'POST', { revision: project.revision, notebook_id }) }
        catch (e) { await refresh().catch(() => {}); throw new Error(`Notebook ${notebook_id} was created but could not be assigned. It remains standalone; retry the pending association before any other application change. ${String(e)}`) }
        await refresh(); await navigate(notebookUrl(notebook_id))
      })}>New notebook in project</button>}</section>
      <section id="project-roadmap"><h2>Roadmap</h2>{roadmaps.length ? <ul>{roadmaps.map(r => <li key={r.id}>{r.data.objective} · {r.data.progress}% · {r.data.tasks.length} tasks</li>)}</ul> : <p>No roadmaps yet. Open a notebook to work on a roadmap.</p>}</section>
      <section id="project-conversations"><h2>Conversations</h2>{conversations.length ? <ul>{conversations.map(c => <li key={c.id}>{c.data.title}</li>)}</ul> : <p>No conversations yet. Open a notebook to start a discussion.</p>}</section>
      <section id="project-activity"><h2>Activity</h2>{activities.length ? <ul>{activities.map(a => <li key={a.id}>{a.data.summary} · {a.data.result}</li>)}</ul> : <p>No activity yet.</p>}</section>
    </>}
  </main>
}

export function NotebookLibrary({ navigate, currentNotebookId, onNotebookDeleted }: BrowserProps & { currentNotebookId?: string; onNotebookDeleted?: (id: string) => void }) {
  const [notebooks, setNotebooks] = useState<Meta[]>([]), [query, setQuery] = useState(''), [loadError, setLoadError] = useState('')
  const [projects, setProjects] = useState<RecordData<Project>[]>([])
  const { busy, error, run } = useOperation()
  const refresh = async () => { const [n, p] = await Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects')]); setNotebooks(n); setProjects(p) }
  useEffect(() => { let live = true; void Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects')]).then(([n, p]) => { if (live) { setNotebooks(n); setProjects(p) } }).catch(e => { if (live) setLoadError(String(e)) }); return () => { live = false } }, [])
  return <main className="browser-page"><h1>Notebooks</h1>{(error || loadError) && <p role="alert" className="alert">{error || loadError}</p>}
    <button disabled={busy} onClick={() => void run(async () => {
      const title = prompt('Notebook title')?.trim(); if (!title) return
      const created = await request<{ metadata: { europa: Meta } }>('/notebooks', json('POST', { title }))
      await navigate(notebookUrl(created.metadata.europa.notebook_id)); await refresh()
    })}>New notebook</button>
    <label>Search notebooks <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
    <ul className="browser-list">{notebooks.filter(n => n.title.toLowerCase().includes(query.toLowerCase())).map(n => <li key={n.notebook_id}>
      <a href={notebookUrl(n.notebook_id)} onClick={e => { e.preventDefault(); void navigate(notebookUrl(n.notebook_id)) }}>{n.title}</a>
      {(() => { const p = projects.find(p => p.data.notebooks.some(member => member.notebook_id === n.notebook_id)); return p ? <a href={projectUrl(p.id)} onClick={e => { e.preventDefault(); void navigate(projectUrl(p.id)) }}>{p.data.title}</a> : <span>Standalone</span> })()}
      <button disabled={busy} onClick={() => void run(async () => {
        const title = prompt('Notebook title', n.title)?.trim(); if (!title) return
        await request(notebookUrl(n.notebook_id), json('PATCH', { revision: n.revision, title })); await refresh()
      })}>Rename</button>
      <button disabled={busy || !!localStorage.getItem(`europa.drafts.${n.notebook_id}`)} title={localStorage.getItem(`europa.drafts.${n.notebook_id}`) ? 'Save or discard browser drafts in the editor before deleting this notebook.' : undefined} onClick={() => void run(async () => {
        if (localStorage.getItem(`europa.drafts.${n.notebook_id}`)) throw new Error('Save or discard browser drafts before deleting this notebook.')
        if (!confirm(`Delete notebook ${n.title}? The saved document will be removed; project history remains.${currentNotebookId === n.notebook_id ? ' This also closes your previously selected notebook.' : ''}`)) return
        await request(notebookUrl(n.notebook_id) + `?revision=${n.revision}`, { method: 'DELETE' })
        if (localStorage.getItem('europa.notebook') === n.notebook_id) localStorage.removeItem('europa.notebook')
        if (n.notebook_id === currentNotebookId) {
          if (onNotebookDeleted) onNotebookDeleted(n.notebook_id)
          else { location.reload(); return } // Clear an editor controller still holding the deleted document.
        } else onNotebookDeleted?.(n.notebook_id)
        await refresh()
      })}>Delete</button>{localStorage.getItem(`europa.drafts.${n.notebook_id}`) && <small>Save or discard browser drafts before deleting.</small>}</li>)}</ul>
  </main>
}

export function ActivityPage({ navigate }: BrowserProps) {
  const [projects, setProjects] = useState<RecordData<Project>[]>([]), [selected, setSelected] = useState(''), [activities, setActivities] = useState<RecordData<Activity>[]>([]), [error, setError] = useState('')
  useEffect(() => { let live = true; void all<Project>('/projects').then(p => { if (live) setProjects(p) }).catch(e => { if (live) setError(String(e)) }); return () => { live = false } }, [])
  useEffect(() => { let live = true; setActivities([]); if (selected) void all<Activity>(`${projectUrl(selected)}/activities`).then(a => { if (live) setActivities(a) }).catch(e => { if (live) setError(String(e)) }); return () => { live = false } }, [selected])
  return <main className="browser-page"><h1>Activity</h1>{error && <p role="alert" className="alert">{error}</p>}
    <label>Project <select value={selected} onChange={e => setSelected(e.target.value)}><option value="">Select a project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.data.title}</option>)}</select></label>
    {!selected ? <p>Select a project to see its activity.</p> : <ul className="browser-list">{activities.map(a => <li key={a.id}><span>{a.data.summary} · {a.data.result}</span>{a.data.notebook_id && <button onClick={() => void navigate(notebookUrl(a.data.notebook_id!))}>Open notebook</button>}</li>)}</ul>}
  </main>
}
