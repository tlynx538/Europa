export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : message } catch { /* preserve status */ }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function json(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body) }
}

export interface Receipt { execution_id: string; cell_id: string; state: string; result?: { status: string } }
export const terminalReceipt = (state: string) => ['applied', 'conflict', 'uncertain'].includes(state)

// A transport failure is not a terminal result and must never imply cancellation.
export async function pollReceipt(notebook: string, execution: string, signal: AbortSignal,
  onReceipt: (receipt: Receipt) => void, delay = 1000): Promise<void> {
  while (!signal.aborted) {
    const receipt = await request<Receipt>(`/notebooks/${encodeURIComponent(notebook)}/executions/${encodeURIComponent(execution)}`, { signal })
    onReceipt(receipt)
    if (terminalReceipt(receipt.state)) return
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, delay)
      const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
    })
  }
}
