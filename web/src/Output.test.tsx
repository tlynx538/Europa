// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Outputs } from './Output'

describe('untrusted notebook output', () => {
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
