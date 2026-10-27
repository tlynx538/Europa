import { useMemo } from 'react'
import { previewDocument, rasterData } from './renderSafety'

export interface Output { output_type: string; name?: string; text?: string | string[]; traceback?: string[]; data?: Record<string, unknown> }
const text = (value: unknown): string => Array.isArray(value) ? value.join('') : typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? ''

function Markup({ value }: { value: string }) {
  const doc = useMemo(() => previewDocument(value), [value])
  return <iframe title="Isolated notebook output" sandbox="" referrerPolicy="no-referrer" srcDoc={doc} />
}

// Deliberately small static Plotly subset: data only, no Plotly JS, URLs, templates,
// callbacks or third-party resources. Unsupported traces remain visible as JSON.
function StaticPlot({ value }: { value: unknown }) {
  const data = (value as { data?: unknown[] } | null)?.data
  if (!Array.isArray(data) || data.length > 20) return <pre>{text(value)}</pre>
  const points = data.flatMap((trace: any, group) => {
    if (!['scatter', 'bar', undefined].includes(trace?.type) || !Array.isArray(trace?.y)) return []
    return trace.y.slice(0, 2000).flatMap((y: unknown, i: number) => typeof y === 'number' && Number.isFinite(y)
      ? [{ x: typeof trace.x?.[i] === 'number' && Number.isFinite(trace.x[i]) ? trace.x[i] : i, y, group, bar: trace.type === 'bar' }] : [])
  })
  if (!points.length) return <pre>{text(value)}</pre>
  const xs = points.map(p => p.x), ys = points.map(p => p.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(0, ...ys), maxY = Math.max(0, ...ys)
  const x = (v: number) => 25 + (v - minX) / (maxX - minX || 1) * 450
  const y = (v: number) => 225 - (v - minY) / (maxY - minY || 1) * 200
  return <figure><svg viewBox="0 0 500 250" role="img" aria-label="Static Plotly data preview">
    <path d={`M25 20V${y(0)}H490`} fill="none" stroke="#64748b" />
    {points.map((p, i) => p.bar ? <rect key={i} x={x(p.x) - 4} y={Math.min(y(0), y(p.y))} width="8" height={Math.abs(y(0) - y(p.y))} fill="#2563eb" /> : <circle key={i} cx={x(p.x)} cy={y(p.y)} r="3" fill={['#2563eb', '#b45309', '#047857'][p.group % 3]} />)}
  </svg><figcaption>Static scatter/bar preview. Interactive features and layout are not executed.</figcaption><details><summary>Original plot data</summary><pre>{text(value)}</pre></details></figure>
}

export function Outputs({ outputs }: { outputs: Output[] }) {
  return <div className="outputs">{outputs.map((output, index) => {
    if (output.output_type === 'stream') return <pre key={index}>{text(output.text)}</pre>
    if (output.output_type === 'error') return <pre className="error" key={index}>{text(output.traceback).replace(/\x1b\[[0-9;]*m/g, '')}</pre>
    const data = output.data ?? {}
    let rendered
    if ('text/html' in data || 'image/svg+xml' in data) rendered = <Markup value={text(data['text/html'] ?? data['image/svg+xml'])} />
    else if ('image/png' in data || 'image/jpeg' in data) {
      const mime = 'image/png' in data ? 'image/png' : 'image/jpeg'
      const url = rasterData(mime, text(data[mime]))
      rendered = url ? <img src={url} alt="Notebook raster output" onError={e => { e.currentTarget.alt = 'Invalid raster output'; e.currentTarget.removeAttribute('src') }} /> : <p>Invalid or oversized raster output.</p>
    } else if ('application/vnd.plotly.v1+json' in data) rendered = <StaticPlot value={data['application/vnd.plotly.v1+json']} />
    else if ('application/json' in data) rendered = <pre>{JSON.stringify(data['application/json'], null, 2)}</pre>
    else if ('text/plain' in data) rendered = <pre>{text(data['text/plain'])}</pre>
    else rendered = <p>Unsupported output is preserved in the notebook.</p>
    return <div key={index}>{rendered}{Object.keys(data).some(m => m.includes('javascript') || m.includes('widget')) && <p>Executable JavaScript/widgets are disabled.</p>}</div>
  })}</div>
}
