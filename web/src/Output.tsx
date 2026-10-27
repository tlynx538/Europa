type OutputValue = string | string[] | unknown
export interface Output { output_type: string; name?: string; text?: string | string[]; traceback?: string[]; data?: Record<string, OutputValue> }
const text = (value: unknown): string => Array.isArray(value) ? value.join('') : typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? ''

// Never interpret output markup in the application origin. Unsupported rich MIME
// stays in the document; it is not silently removed or executed.
export function Outputs({ outputs }: { outputs: Output[] }) {
  return <div className="outputs">{outputs.map((output, index) => {
    if (output.output_type === 'stream') return <pre key={index}>{text(output.text)}</pre>
    if (output.output_type === 'error') return <pre className="error" key={index}>{text(output.traceback)}</pre>
    const data = output.data ?? {}
    if ('text/plain' in data) return <pre key={index}>{text(data['text/plain'])}</pre>
    if ('application/json' in data) return <pre key={index}>{JSON.stringify(data['application/json'], null, 2)}</pre>
    return <p key={index}>Rich output is saved but cannot be safely displayed yet.</p>
  })}</div>
}
