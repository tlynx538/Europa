// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Outputs } from './Output'

describe('untrusted notebook output', () => {
  it('only labels expression results with their original count, in output order', () => {
    const html = renderToStaticMarkup(<Outputs outputs={[
      { output_type: 'stream', text: 'stdout' },
      { output_type: 'display_data', data: { 'text/plain': 'display' } },
      { output_type: 'execute_result', execution_count: 4, data: { 'text/plain': 'result' } },
      { output_type: 'execute_result', execution_count: null, data: { 'text/plain': 'historical result' } },
      { output_type: 'error', traceback: ['ValueError: error'] },
    ]} />)
    expect(html.match(/Out \[/g)).toHaveLength(2)
    expect(html).toContain('Out [4]:')
    expect(html).toContain('Out [ ]:')
    expect(html).not.toContain('Out [9]:')
    expect(['stdout', 'display', 'Out [4]:', 'result', 'ValueError: error'].map(part => html.indexOf(part))).toEqual([...['stdout', 'display', 'Out [4]:', 'result', 'ValueError: error'].map(part => html.indexOf(part))].sort((a, b) => a - b))
  })
  it('escapes plain text and does not render HTML or JavaScript MIME', () => {
    const html = renderToStaticMarkup(<Outputs outputs={[
      { output_type: 'stream', text: '<script>alert(1)</script>' },
      { output_type: 'display_data', data: { 'text/html': '<img src="https://example.com/a" onerror="alert(1)">', 'application/javascript': 'alert(1)' } },
    ]} />)
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('https://example.com')
    expect(html).toContain('sandbox=""')
    expect(html).toContain('script-src')
  })
})
