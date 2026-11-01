type IconName = 'save' | 'add' | 'cut' | 'copy' | 'paste' | 'run' | 'interrupt' | 'up' | 'down'

export function EditorIcon({ name }: { name: IconName }) {
  const shared = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true as const }
  const paths = {
    save: <><path d="M4 3h13l3 3v15H4zM7 3v7h9V3M7 21v-8h10v8" /><path d="M13 5v3" /></>,
    add: <path d="M12 4v16M4 12h16" />,
    cut: <><circle cx="6" cy="18" r="2" /><circle cx="18" cy="18" r="2" /><path d="M4 4l15 12M20 4 5 16" /></>,
    copy: <><rect x="8" y="7" width="12" height="14" rx="1" /><path d="M16 7V3H4v14h4" /></>,
    paste: <><rect x="4" y="5" width="16" height="16" rx="1" /><path d="M9 5V3h6v2M8 11h8M8 15h6" /></>,
    run: <path d="M7 4v16l13-8z" />,
    interrupt: <rect x="5" y="5" width="14" height="14" rx="1" fill="currentColor" stroke="none" />,
    up: <path d="M12 19V5m-6 6 6-6 6 6" />,
    down: <path d="M12 5v14m-6-6 6 6 6-6" />,
  }
  return <svg {...shared}>{paths[name]}</svg>
}
