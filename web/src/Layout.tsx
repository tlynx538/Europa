import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

const Panels = createContext<{ left: boolean; right: boolean; setLeft: (v: boolean) => void; setRight: (v: boolean) => void; mobile: boolean }>({ left: true, right: true, setLeft: () => {}, setRight: () => {}, mobile: false })
export const usePanels = () => useContext(Panels)
const storedWidth = (side: string, fallback: number) => Math.max(220, Math.min(480, Number(localStorage.getItem(`europa.width.${side}`)) || fallback))

export function CommentIndicator({ cell, count }: { cell: string; count: number }) {
  const { setRight } = useContext(Panels)
  return <a href="#discussion" onClick={event => {
    event.preventDefault(); setRight(true)
    requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-comment-cell="${CSS.escape(cell)}"]`)?.focus())
  }}>{count} comments</a>
}

export function Layout({ children }: { children: ReactNode }) {
  const [mobile, setMobile] = useState(() => matchMedia('(max-width: 850px)').matches)
  const [left, setLeft] = useState(() => !mobile && localStorage.getItem('europa.panel.left') !== 'false')
  const [right, setRight] = useState(() => !mobile && localStorage.getItem('europa.panel.right') !== 'false')
  const [leftWidth, setLeftWidth] = useState(() => storedWidth('left', 270))
  const [rightWidth, setRightWidth] = useState(() => storedWidth('right', 310))
  useEffect(() => {
    const query = matchMedia('(max-width: 850px)')
    const change = () => { setMobile(query.matches); setLeft(!query.matches && localStorage.getItem('europa.panel.left') !== 'false'); setRight(!query.matches && localStorage.getItem('europa.panel.right') !== 'false') }
    query.addEventListener('change', change); return () => query.removeEventListener('change', change)
  }, [])
  useEffect(() => { if (!mobile) { localStorage.setItem('europa.panel.left', String(left)); localStorage.setItem('europa.panel.right', String(right)) } }, [left, right, mobile])
  const resize = (side: 'left' | 'right', value: number) => {
    const width = Math.max(220, Math.min(480, value))
    if (side === 'left') setLeftWidth(width); else setRightWidth(width)
    localStorage.setItem(`europa.width.${side}`, String(width))
  }
  const handle = (side: 'left' | 'right', value: number) => <div className={`splitter splitter-${side}`} role="separator" aria-label={`Resize ${side} sidebar`} aria-orientation="vertical" aria-valuenow={value} aria-valuemin={220} aria-valuemax={480} tabIndex={0}
    onKeyDown={e => { if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); resize(side, value + (e.key === 'ArrowRight' ? 20 : -20) * (side === 'left' ? 1 : -1)) } }}
    onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId) }}
    onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) resize(side, side === 'left' ? e.clientX : innerWidth - e.clientX) }} />
  return <Panels.Provider value={{ left, right, setLeft, setRight, mobile }}>
    <nav className="appbar"><strong>Europa</strong><button aria-expanded={left} onClick={() => setLeft(!left)}>Roadmap</button><button aria-expanded={right} onClick={() => setRight(!right)}>Discussion</button><span>Owner workspace · localhost</span></nav>
    <div className="shell" style={{ gridTemplateColumns: `${left ? `${leftWidth}px 8px` : '0px 0px'} minmax(0,1fr) ${right ? `8px ${rightWidth}px` : '0px 0px'}` }}>
      {children}{!mobile && left && handle('left', leftWidth)}{!mobile && right && handle('right', rightWidth)}
    </div>
  </Panels.Provider>
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
  return <dialog ref={ref} className={`side-panel ${side === 'left' ? 'roadmap-panel' : 'discussion-panel'}`} id={side === 'right' ? 'discussion' : 'roadmap'} aria-label={side === 'left' ? 'Project and roadmap' : 'Discussion'} onCancel={() => change(false)}>
    <button className="close-panel" onClick={() => change(false)}>Close {side === 'left' ? 'roadmap' : 'discussion'}</button>{children}
  </dialog>
}
