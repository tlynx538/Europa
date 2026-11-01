import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const Panels = createContext<{ left: boolean; right: boolean; setLeft: (v: boolean) => void; setRight: (v: boolean) => void; mobile: boolean; settingsOpen: boolean; openSettings: () => void; closeSettings: () => void }>({ left: false, right: false, setLeft: () => {}, setRight: () => {}, mobile: false, settingsOpen: false, openSettings: () => {}, closeSettings: () => {} })
export const usePanels = () => useContext(Panels)
const HeaderTarget = createContext<HTMLElement | null>(null)
const StatusTarget = createContext<HTMLElement | null>(null)
export function HeaderSlot({ children }: { children: ReactNode }) {
  const target = useContext(HeaderTarget)
  return target ? createPortal(children, target) : null
}
export function StatusSlot({ children }: { children: ReactNode }) {
  const target = useContext(StatusTarget)
  return target ? createPortal(children, target) : null
}
const storedWidth = (side: string, fallback: number) => Math.max(220, Math.min(480, Number(localStorage.getItem(`europa.width.${side}`)) || fallback))

export function CommentIndicator({ cell, count }: { cell: string; count: number }) {
  return <a href="#discussion" onClick={event => {
    event.preventDefault()
    requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-comment-cell="${CSS.escape(cell)}"]`)?.focus())
  }}>{count} comments</a>
}

export function Layout({ children }: { children: ReactNode }) {
  const [headerTarget, setHeaderTarget] = useState<HTMLElement | null>(null)
  const [statusTarget, setStatusTarget] = useState<HTMLElement | null>(null)
  const appbar = useRef<HTMLElement>(null)
  const statusbar = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = appbar.current, footer = statusbar.current
    if (!node || !footer) return
    const update = () => { document.documentElement.style.setProperty('--appbar-height', `${node.getBoundingClientRect().height}px`); document.documentElement.style.setProperty('--status-height', `${footer.getBoundingClientRect().height}px`) }
    const observer = new ResizeObserver(update)
    observer.observe(node)
    observer.observe(footer)
    update()
    return () => { observer.disconnect(); document.documentElement.style.removeProperty('--appbar-height'); document.documentElement.style.removeProperty('--status-height') }
  }, [])
  const [mobile, setMobile] = useState(() => matchMedia('(max-width: 1100px)').matches)
  const [left, setLeft] = useState(() => !mobile && localStorage.getItem('europa.panel.left') === 'true')
  const right = false
  const setRight = (value: boolean) => { if (value) document.querySelector<HTMLTextAreaElement>('.message-composer textarea')?.focus() }
  const [settingsOpen, setSettingsOpen] = useState(false)
  const openSettings = () => { if (mobile) { setLeft(false); setRight(false) } setSettingsOpen(true) }
  const closeSettings = () => setSettingsOpen(false)
  const [leftWidth, setLeftWidth] = useState(() => storedWidth('left', 270))
  useEffect(() => {
    const query = matchMedia('(max-width: 1100px)')
    const change = () => { setMobile(query.matches); setLeft(!query.matches && localStorage.getItem('europa.panel.left') === 'true') }
    query.addEventListener('change', change); return () => query.removeEventListener('change', change)
  }, [])
  useEffect(() => { if (!mobile) localStorage.setItem('europa.panel.left', String(left)) }, [left, mobile])
  const resize = (side: 'left' | 'right', value: number) => {
    const width = Math.max(220, Math.min(480, value))
    if (side === 'left') setLeftWidth(width)
    localStorage.setItem(`europa.width.${side}`, String(width))
  }
  const handle = (side: 'left' | 'right', value: number) => <div className={`splitter splitter-${side}`} role="separator" aria-label={`Resize ${side} sidebar`} aria-orientation="vertical" aria-valuenow={value} aria-valuemin={220} aria-valuemax={480} tabIndex={0}
    onKeyDown={e => { if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); resize(side, value + (e.key === 'ArrowRight' ? 20 : -20) * (side === 'left' ? 1 : -1)) } }}
    onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId) }}
    onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) resize(side, side === 'left' ? e.clientX : innerWidth - e.clientX) }} />
  return <Panels.Provider value={{ left, right, setLeft, setRight, mobile, settingsOpen, openSettings, closeSettings }}><HeaderTarget.Provider value={headerTarget}><StatusTarget.Provider value={statusTarget}>
    <nav ref={appbar} className="appbar" aria-label="Notebook workspace"><div className="appbar-switchers" ref={setHeaderTarget} /></nav>
    <div className="shell" style={{ gridTemplateColumns: `${left ? `minmax(0, ${leftWidth}px) 8px` : '0px 0px'} minmax(0,1fr) 0px 0px` }}>
      {children}{!mobile && left && handle('left', leftWidth)}
    </div>
    <div ref={node => { statusbar.current = node; setStatusTarget(node) }} className="global-status" />
  </StatusTarget.Provider></HeaderTarget.Provider></Panels.Provider>
}

export function SidePanel({ side, children }: { side: 'left' | 'right'; children: ReactNode }) {
  const context = useContext(Panels)
  const open = context[side], change = side === 'left' ? context.setLeft : context.setRight
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    dialog.close()
    if (open) { if (context.mobile) dialog.showModal(); else dialog.show() }
  }, [open, context.mobile])
  return <dialog ref={ref} className={`side-panel ${side === 'left' ? 'roadmap-panel' : 'discussion-panel'}`} id={side === 'right' ? 'discussion' : 'roadmap'} aria-label={side === 'left' ? 'Project and roadmap' : 'Discussion'} onCancel={() => change(false)} onClose={() => {
    if (context.mobile && side === 'left') requestAnimationFrame(() => Array.from(document.querySelectorAll<HTMLElement>('.notebook-menubar [aria-haspopup=menu]')).find(node => node.textContent === 'View')?.focus())
  }}>
    <button className="close-panel" onClick={() => change(false)}>Close {side === 'left' ? 'roadmap' : 'discussion'}</button>{children}
  </dialog>
}
