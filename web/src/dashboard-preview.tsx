import { useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'
import { Dashboard, type DashboardEntry, type DashboardSession } from './Dashboard'

// Disposable Checkpoint A data. This entry point never imports the application
// controller or API client and is not included in the production index.html.
const file = (name: string, kind: DashboardEntry['kind'], size: string | null = null, status?: DashboardEntry['status']): DashboardEntry => ({ name, kind, modified: 'Nov 10, 2026, 9:42 AM', size, status })
const initial: Record<string, DashboardEntry[]> = {
  '': [file('Customer Analytics', 'project'), file('Experiments', 'folder'), file('Shared notes', 'folder'), file('Welcome.ipynb', 'notebook', '8.2 KB', 'Stopped'), file('README.md', 'markdown', '1.4 KB'), file('requirements.txt', 'text', '246 B')],
  'Customer Analytics': [file('Experiments', 'folder'), file('Reports', 'folder'), file('Customer segments.ipynb', 'notebook', '24.6 KB', 'Idle'), file('Revenue forecast.ipynb', 'notebook', '18.3 KB', 'Busy'), file('clean_data.py', 'python', '3.1 KB'), file('README.md', 'markdown', '2.4 KB'), file('notes.txt', 'text', '682 B'), file('customers.parquet', 'file', '2.8 MB')],
  'Customer Analytics/Experiments': [], Experiments: [], 'Shared notes': [], 'Customer Analytics/Reports': [],
}
const sessions: DashboardSession[] = [
  { id: 'segments', title: 'Customer segments.ipynb', location: 'Customer Analytics', state: 'Idle', activity: 'Nov 10, 2026, 9:42 AM' },
  { id: 'forecast', title: 'Revenue forecast.ipynb', location: 'Customer Analytics', state: 'Busy', activity: 'Nov 10, 2026, 9:45 AM' },
  { id: 'exploration', title: 'Exploration.ipynb', location: 'Experiments', state: 'Unknown', activity: null },
]
function Preview() {
  const [path, setPath] = useState<string[]>([]), [tab, setTab] = useState<'files' | 'running'>('files')
  const [directories, setDirectories] = useState(initial), [state, setState] = useState('populated')
  const [createKind, setCreateKind] = useState<'notebook' | 'project' | 'folder' | 'text'>('notebook'), [name, setName] = useState('')
  const createDialog = useRef<HTMLDialogElement>(null), openDialog = useRef<HTMLDialogElement>(null)
  const [opened, setOpened] = useState('')
  const open = (entry: DashboardEntry) => {
    if (entry.kind === 'folder' || entry.kind === 'project') { setPath([...path, entry.name]); return }
    setOpened(entry.name); openDialog.current?.showModal()
  }
  return <><aside className="dashboard-preview-note" aria-label="Fixture preview">Checkpoint A · Disposable fixture data · No filesystem or runtime actions<label style={{ margin: 0 }}>Scenario <select value={state} onChange={event => setState(event.target.value)}><option value="populated">Populated</option><option value="empty">Empty</option><option value="loading">Loading</option><option value="error">Unavailable</option></select></label></aside>
    <Dashboard key={path.join('/')} path={path} tab={tab} onTab={setTab} entries={state === 'empty' ? [] : directories[path.join('/')] ?? []} sessions={state === 'empty' ? [] : sessions} recents={[{ id: 'segments', title: 'Customer segments.ipynb', location: 'Home / Customer Analytics' }, { id: 'exploration', title: 'Exploration.ipynb', location: 'Home / Experiments' }]} loading={state === 'loading'} error={state === 'error' ? tab === 'running' ? 'Runtime observation unavailable. Session status is Unknown.' : 'Directory unavailable.' : undefined} onDirectory={setPath} onOpen={open} onSession={id => { setOpened(sessions.find(session => session.id === id)!.title); openDialog.current?.showModal() }} onRecent={id => { setOpened(sessions.find(session => session.id === id)!.title); openDialog.current?.showModal() }} onRefresh={() => setState('populated')} onCreate={kind => { setCreateKind(kind); setName(''); createDialog.current?.showModal() }} />
    <dialog ref={createDialog} className="dashboard-dialog" aria-labelledby="create-title"><form onSubmit={event => {
      event.preventDefault()
      const value = name.trim()
      if (!value || value.startsWith('.') || /[/\\]/.test(value)) return
      const filename = createKind === 'notebook' && !value.endsWith('.ipynb') ? `${value}.ipynb` : value
      if ((directories[path.join('/')] ?? []).some(entry => entry.name === filename)) return
      setDirectories(previous => ({ ...previous, [path.join('/')]: [...(previous[path.join('/')] ?? []), { name: filename, kind: createKind, modified: null, size: null }] }))
      createDialog.current?.close()
    }}><h2 id="create-title">New {createKind} (fixture)</h2><p>Home{path.length ? ` / ${path.join(' / ')}` : ''}</p><label>Name<input autoFocus required value={name} onChange={event => setName(event.target.value)} /></label><p>Preview only. Nothing is written to disk.</p><button type="button" onClick={() => createDialog.current?.close()}>Cancel</button> <button type="submit">Create fixture</button></form></dialog>
    <dialog ref={openDialog} className="dashboard-dialog" aria-labelledby="open-title"><h2 id="open-title">{opened}</h2><p>Opening the existing editor will be connected after visual review.</p><form method="dialog"><button>Close</button></form></dialog>
  </>
}
createRoot(document.getElementById('root')!).render(<Preview />)
