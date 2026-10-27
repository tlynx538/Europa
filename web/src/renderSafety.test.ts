// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { previewDocument, rasterData } from './renderSafety'

describe('isolated MIME previews', () => {
  it('strips active content, external URLs and CSS while preserving tables and static SVG', () => {
    const html = previewDocument('<table><tr><td>42</td></tr></table><svg><circle r="4" onload="alert(1)" fill="red"/><image href="https://remote.invalid"/><foreignObject><img src=x></foreignObject><animate attributeName="href"/></svg><style>body{background:url(https://remote.invalid)}</style><iframe src=x></iframe><meta http-equiv="refresh" content="0;url=https://remote.invalid"><a href="javascript:alert(1)">link</a>')
    expect(html).toContain('<td>42</td>')
    expect(html).toContain('<circle')
    expect(html).not.toMatch(/onload|remote.invalid|foreignObject|<animate|<iframe|javascript:/)
    expect(html).toContain("default-src 'none'")
  })
  it('checks raster signature and dimensions before rendering', () => {
    expect(rasterData('image/png', btoa('<svg onload="alert(1)"/>'))).toBeNull()
    const png = '\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\0\x01\0\0\0\x01'
    expect(rasterData('image/png', btoa(png))).toContain('data:image/png;base64,')
    expect(rasterData('image/png', btoa(png.slice(0, 16) + '\xff\xff\xff\xff\xff\xff\xff\xff'))).toBeNull()
    expect(rasterData('image/jpeg', '%%%')).toBeNull()
  })
})
