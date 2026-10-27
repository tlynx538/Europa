import { useEffect, useRef, useState } from 'react'
import { type Task, type TaskStatus } from './applicationApi'

export function TaskEditor({ initial, siblings, onSave, onClose }: { initial: Task; siblings: Task[]; onSave: (task: Task) => Promise<void>; onClose: () => void }) {
  const [task, setTask] = useState(initial)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current!.showModal() }, [])
  return <dialog ref={ref} className="editor-dialog" onCancel={onClose}><form onSubmit={e => {
    e.preventDefault(); setBusy(true); setError('')
    void onSave(task).then(onClose).catch(e => setError(String(e))).finally(() => setBusy(false))
  }}><h2>Edit task</h2>{error && <p role="alert" className="alert">{error}</p>}
    <label>Title<input required maxLength={256} value={task.title} onChange={e => setTask({ ...task, title: e.target.value })} /></label>
    <label>Acceptance criteria (one per line)<textarea required value={task.acceptance_criteria.join('\n')} onChange={e => setTask({ ...task, acceptance_criteria: e.target.value.split('\n') })} /></label>
    <label>Status<select aria-label="Status" value={task.status} onChange={e => setTask({ ...task, status: e.target.value as TaskStatus, accepted: false })}>{['pending', 'in_progress', 'blocked', 'needs_input', 'completed', 'cancelled'].map(s => <option key={s}>{s}</option>)}</select></label>
    <label>Explanation<textarea required={['blocked', 'needs_input'].includes(task.status)} value={task.explanation ?? ''} onChange={e => setTask({ ...task, explanation: e.target.value || null })} /></label>
    <label>Weight<input type="number" min="0.001" max="1000" step="any" required value={task.weight} onChange={e => setTask({ ...task, weight: Number(e.target.value) })} /></label>
    <label>Parent<select value={task.parent_id ?? ''} onChange={e => setTask({ ...task, parent_id: e.target.value || null })}><option value="">None</option>{siblings.filter(t => t.id !== task.id).map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
    <fieldset><legend>Dependencies</legend>{siblings.filter(t => t.id !== task.id).map(t => <label key={t.id}><input type="checkbox" checked={task.dependencies.includes(t.id)} onChange={e => setTask({ ...task, dependencies: e.target.checked ? [...task.dependencies, t.id] : task.dependencies.filter(id => id !== t.id) })} />{t.title}</label>)}</fieldset>
    <label>Linked notebook ID<input value={task.links.notebook_id ?? ''} onChange={e => setTask({ ...task, links: { ...task.links, notebook_id: e.target.value || null } })} /></label>
    <label>Linked cell ID<input value={task.links.cell_id ?? ''} onChange={e => setTask({ ...task, links: { ...task.links, cell_id: e.target.value || null } })} /></label>
    <label>Linked message ID<input value={task.links.message_id ?? ''} onChange={e => setTask({ ...task, links: { ...task.links, message_id: e.target.value || null } })} /></label>
    <label>Linked execution receipt ID<input value={task.links.execution_receipt_id ?? ''} onChange={e => setTask({ ...task, links: { ...task.links, execution_receipt_id: e.target.value || null } })} /></label>
    {task.status === 'completed' && <><label><input type="checkbox" required checked={task.accepted} onChange={e => setTask({ ...task, accepted: e.target.checked })} />I accept this task as the owner</label>
      <label>Acceptance evidence (one summary per line)<textarea required value={task.evidence.map(e => e.summary).join('\n')} onChange={e => setTask({ ...task, evidence: e.target.value.split('\n').map((summary, i) => ({ ...task.evidence[i], summary })) })} /></label></>}
    <p>Dependencies, links and acceptance are validated by the server. A stale revision will not overwrite another plan.</p>
    <button disabled={busy}>Save task</button><button type="button" disabled={busy} onClick={onClose}>Cancel</button>
  </form></dialog>
}
