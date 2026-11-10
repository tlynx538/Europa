import { useEffect, useRef, useState, type ReactNode } from 'react'
import * as Menubar from '@radix-ui/react-menubar'
import { ActionItem, ActionMenu } from './ActionMenu'
import './dashboard.css'

export type DashboardEntry = { name: string; kind: 'folder' | 'project' | 'notebook' | 'python' | 'markdown' | 'text' | 'file'; modified: string | null; size: string | null; status?: SessionState }
export type SessionState = 'Busy' | 'Idle' | 'Starting' | 'Stopped' | 'Needs attention' | 'Unknown'
export type DashboardSession = { id: string; title: string; location: string; state: SessionState; activity: string | null; available?: boolean; reason?: string; observedAt?: number | null }
export type DashboardRecent = { id: string; title: string; location: string; available?: boolean; openedAt?: number }
type Props = {
  path: string[]; entries: DashboardEntry[]; sessions: DashboardSession[]; recents: DashboardRecent[]
  tab: 'files' | 'running'; onTab: (tab: 'files' | 'running') => void
  onDirectory: (path: string[]) => void; onOpen: (entry: DashboardEntry) => void
  onSession: (id: string) => void; onRecent: (id: string) => void
  onCreate: (kind: 'notebook' | 'project' | 'folder' | 'text') => void; onRefresh: () => void
  loading?: boolean; error?: string; sessionError?: string; notice?: ReactNode; actionsDisabled?: boolean
  onHome?: () => void; onCatalog?: (kind: 'projects' | 'notebooks') => void; onMore?: () => void; recentUnavailable?: boolean; recentError?: string; onRecentMenu?: () => void
  sessionNotice?: string; onSessionsMore?: () => void; onSessionsPrevious?: () => void
}

function FileIcon({ kind }: { kind: DashboardEntry['kind'] }) {
  const folder = kind === 'folder' || kind === 'project'
  return <svg className={`dashboard-icon icon-${kind}`} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
    {folder ? <><path d="M2 5h6l2 2h8v10H2Z" fill="currentColor" fillOpacity=".12" />{kind === 'project' && <path d="M8 10h4v4H8z" />}</> : <><path d="M4 2h8l4 4v12H4Z" /><path d="M12 2v5h4" />{kind === 'notebook' ? <><path d="M7 10h6M7 13h6" /><path d="M2 5h4M2 9h4M2 13h4" /></> : kind === 'python' ? <path d="m8 9-2 2 2 2m4-4 2 2-2 2" /> : kind === 'markdown' ? <path d="M6 14v-4l2 2 2-2v4m2-3 1 2 1-2" /> : kind === 'text' ? <path d="M7 10h6M7 13h6" /> : null}</>}
  </svg>
}
function Status({ state }: { state: SessionState }) {
  return <span className={`dashboard-status state-${state.toLowerCase().replaceAll(' ', '-')}`}><i aria-hidden="true" />{state}</span>
}
function Menu({ name, children }: { name: string; children: ReactNode }) {
  return <Menubar.Menu><Menubar.Trigger className="menu-trigger">{name}</Menubar.Trigger><Menubar.Portal><Menubar.Content className="menu-content" align="start" sideOffset={3}>{children}</Menubar.Content></Menubar.Portal></Menubar.Menu>
}

/** Presentation only. All data, navigation and mutations come from the caller. */
export function Dashboard(props: Props) {
  const { path, entries, sessions, recents, tab, onTab } = props
  const [selection, setSelection] = useState<string[]>([]), [query, setQuery] = useState('')
  const [showModified, setShowModified] = useState(true), [dialog, setDialog] = useState<'help' | 'settings' | null>(null)
  const info = useRef<HTMLDialogElement>(null), selectAll = useRef<HTMLInputElement>(null)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const visible = entries.filter(entry => entry.name.toLowerCase().includes(query.toLowerCase())).sort((a, b) => Number(['folder', 'project'].includes(b.kind)) - Number(['folder', 'project'].includes(a.kind)) || a.name.localeCompare(b.name))
  const selected = visible.filter(entry => selection.includes(entry.name))
  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = selected.length > 0 && selected.length < visible.length }, [selected.length, visible.length])
  const go = (target: string[]) => { setSelection([]); setQuery(''); props.onDirectory(target) }
  const showDialog = (value: 'help' | 'settings') => { setDialog(value); info.current?.showModal() }
  const checkbox = (entry: DashboardEntry) => <input type="checkbox" aria-label={`Select ${entry.name}`} checked={selection.includes(entry.name)} onChange={event => setSelection(value => event.target.checked ? [...value, entry.name] : value.filter(name => name !== entry.name))} />
  const newMenu = <ActionMenu label="New" trigger="New" disabled={props.actionsDisabled}><ActionItem className="menu-row" onSelect={() => props.onCreate('notebook')}>Notebook · Python 3</ActionItem><ActionItem className="menu-row" onSelect={() => props.onCreate('project')}>Project</ActionItem><ActionItem className="menu-row" onSelect={() => props.onCreate('folder')}>Folder</ActionItem><ActionItem className="menu-row" onSelect={() => props.onCreate('text')}>Text file</ActionItem></ActionMenu>
  return <div className="dashboard">
    <header className="dashboard-header"><div><a href="/" className="dashboard-wordmark" onClick={event => { event.preventDefault(); props.onHome ? props.onHome() : (go([]), onTab('files')) }}><span aria-hidden="true" className="europa-mark" />Europa</a><span className="dashboard-header-actions"><button onClick={() => showDialog('settings')}>Preferences</button><span>Notebook workspace</span></span></div></header>
    <div className="dashboard-menu"><Menubar.Root aria-label="Workspace menu"><Menu name="File"><Menubar.Item className="menu-row" onSelect={() => { props.onHome ? props.onHome() : (go([]), onTab('files')) }}>Home</Menubar.Item><Menubar.Item className="menu-row" disabled={selected.length !== 1 || props.actionsDisabled} onSelect={() => props.onOpen(selected[0])}>Open selected</Menubar.Item>{props.onCatalog && <><Menubar.Item className="menu-row" onSelect={() => props.onCatalog?.('projects')}>Existing catalog projects</Menubar.Item><Menubar.Item className="menu-row" onSelect={() => props.onCatalog?.('notebooks')}>Existing catalog notebooks</Menubar.Item></>}</Menu><Menu name="View"><Menubar.Item className="menu-row" onSelect={() => onTab('files')}>Files</Menubar.Item><Menubar.Item className="menu-row" onSelect={() => onTab('running')}>Running</Menubar.Item><Menubar.Item className="menu-row" onSelect={props.onRefresh}>Refresh</Menubar.Item></Menu><Menu name="Settings"><Menubar.Item className="menu-row" onSelect={() => showDialog('settings')}>Preferences</Menubar.Item></Menu><Menu name="Help"><Menubar.Item className="menu-row" onSelect={() => showDialog('help')}>Keyboard shortcuts</Menubar.Item></Menu></Menubar.Root></div>
    <main className="dashboard-workspace" aria-label="Workspace">
      <div className="dashboard-tabs" role="tablist" aria-label="Workspace views" onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        event.preventDefault(); const index = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : tab === 'files' ? 1 : 0
        tabs.current[index]?.focus(); onTab(index === 0 ? 'files' : 'running')
      }}>{(['files', 'running'] as const).map((value, index) => <button key={value} ref={node => { tabs.current[index] = node }} id={`tab-${value}`} role="tab" aria-selected={tab === value} aria-controls={`panel-${value}`} tabIndex={tab === value ? 0 : -1} onClick={() => onTab(value)}>{value === 'files' ? 'Files' : 'Running'}</button>)}</div>
      <section id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
        {tab === 'files' ? <>
          <div className="dashboard-pathbar"><nav aria-label="Current directory"><button onClick={() => go([])}>Home</button>{path.map((part, index) => <span key={index}><span aria-hidden="true"> / </span><button aria-current={index === path.length - 1 ? 'location' : undefined} onClick={() => go(path.slice(0, index + 1))}>{part}</button></span>)}</nav></div>
          <div className="dashboard-toolbar"><div className="dashboard-selection" role="status">{selected.length ? <><span>{selected.length} selected</span><button disabled={selected.length !== 1 || props.actionsDisabled} onClick={() => props.onOpen(selected[0])}>Open</button><button onClick={() => setSelection([])}>Clear</button></> : <span>Select items to open them.</span>}</div><div className="dashboard-actions"><ActionMenu trigger="Recent" label="Recent notebooks" onOpenChange={open => { if (open) props.onRecentMenu?.() }}>{recents.length ? recents.map(recent => <ActionItem className="menu-row dashboard-recent" disabled={recent.available === false} title={recent.openedAt ? `Opened ${new Date(recent.openedAt * 1000).toLocaleString()}` : undefined} key={recent.id} onSelect={() => props.onRecent(recent.id)}><span>{recent.title}<small>{recent.location}</small></span></ActionItem>) : <ActionItem className="menu-row" disabled>{props.recentError || (props.recentUnavailable ? 'Recent history integration pending' : 'No recent notebooks')}</ActionItem>}</ActionMenu>{newMenu}<button onClick={props.onRefresh} disabled={props.loading}>Refresh</button></div></div>
          <label className="dashboard-filter"><span>Filter files</span><input type="search" placeholder="Filter by name" value={query} onChange={event => setQuery(event.target.value)} /></label>
          {props.notice}
          {props.error && <p role="alert" className="dashboard-error">{props.error} <button onClick={props.onRefresh}>Try again</button></p>}
          <div className="dashboard-table-scroll"><table className="dashboard-table"><caption className="dashboard-sr">Files in {path.join(' / ') || 'Home'}</caption><thead><tr><th className="dashboard-check"><input ref={selectAll} type="checkbox" aria-label="Select all files" disabled={!visible.length || props.loading} checked={!!visible.length && selected.length === visible.length} onChange={event => setSelection(event.target.checked ? visible.map(entry => entry.name) : [])} /></th><th scope="col">Name <span aria-hidden="true">↑</span></th>{showModified && <th scope="col" className="dashboard-modified">Last modified</th>}<th scope="col" className="dashboard-size">File size</th><th scope="col" className="dashboard-runtime">Runtime</th></tr></thead><tbody onKeyDown={event => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
            const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('.dashboard-filename'))
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
            if (index < 0) return
            event.preventDefault(); buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : Math.min(buttons.length - 1, Math.max(0, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus()
          }}>{!props.loading && !props.error && visible.map(entry => <tr key={entry.name} className={selection.includes(entry.name) ? 'is-selected' : ''}><td>{checkbox(entry)}</td><td><button className="dashboard-filename" onClick={() => { if (entry.kind === 'folder' || entry.kind === 'project') go([...path, entry.name]); else props.onOpen(entry) }}><FileIcon kind={entry.kind} /><span>{entry.name}</span>{entry.kind === 'project' && <small>Project</small>}</button></td>{showModified && <td className="dashboard-modified">{entry.modified ?? '—'}</td>}<td className="dashboard-size">{entry.size ?? '—'}</td><td className="dashboard-runtime">{entry.kind === 'notebook' ? <Status state={entry.status ?? 'Unknown'} /> : '—'}</td></tr>)}</tbody></table></div>
          {props.loading ? <p role="status" className="dashboard-empty">Loading directory…</p> : !props.error && !visible.length && <div className="dashboard-empty"><span>{query ? 'No files match your filter.' : 'This folder is empty.'}</span>{!query && newMenu}</div>}
          {props.onMore && <button onClick={props.onMore} disabled={props.loading}>Load more</button>}
        </> : <><div className="dashboard-toolbar"><h1>Notebooks / Kernels</h1><button onClick={props.onRefresh} disabled={props.loading}>Refresh</button></div>{props.sessionNotice && <p role="status" className="dashboard-observation-note">{props.sessionNotice}</p>}{(props.sessionError || props.error) && <p role="alert" className="dashboard-error">{props.sessionError || props.error}</p>}<div className="dashboard-table-scroll"><table className="dashboard-table dashboard-sessions"><caption className="dashboard-sr">Notebook runtime sessions</caption><thead><tr><th scope="col">Notebook</th><th scope="col">Location</th><th scope="col">State</th><th scope="col" className="dashboard-modified">Last activity</th><th scope="col">Actions</th></tr></thead><tbody>{!props.error && sessions.map(session => <tr key={session.id} title={session.reason ? `${session.reason}${session.observedAt ? ` · Observed ${new Date(session.observedAt * 1000).toLocaleString()}` : ''}` : undefined}><td><button className="dashboard-filename" disabled={session.available === false} onClick={() => props.onSession(session.id)}><FileIcon kind="notebook" />{session.title}</button></td><td>{session.location}</td><td><Status state={session.state} /></td><td className="dashboard-modified">{session.activity ?? '—'}</td><td><button disabled title="Notebook-scoped shutdown has not been verified">Shut down</button></td></tr>)}</tbody></table></div>{props.onSessionsPrevious && <button onClick={props.onSessionsPrevious} disabled={props.loading}>Previous sessions</button>}{props.onSessionsMore && <button onClick={props.onSessionsMore} disabled={props.loading}>Next sessions</button>}{props.loading && !sessions.length ? <p role="status" className="dashboard-empty">Observing sessions…</p> : !props.error && !props.sessionError && !sessions.length && <p className="dashboard-empty">No active notebook sessions.</p>}</>}
      </section>
    </main>
    <dialog ref={info} className="dashboard-dialog" aria-labelledby="dashboard-dialog-title"><h2 id="dashboard-dialog-title">{dialog === 'settings' ? 'Preferences' : 'Keyboard shortcuts'}</h2>{dialog === 'settings' ? <label><input type="checkbox" checked={showModified} onChange={event => setShowModified(event.target.checked)} /> Show last modified column</label> : <p>Tab moves between controls. Arrow keys switch tabs and move between file names. Space selects a checkbox. Enter opens a file or folder. Escape dismisses menus.</p>}<form method="dialog"><button>Close</button></form></dialog>
  </div>
}
