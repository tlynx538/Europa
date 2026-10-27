import DOMPurify from 'dompurify'

export function previewDocument(markup: string): string {
  const clean = DOMPurify.sanitize(markup.slice(0, 4_000_000), {
    ALLOWED_TAGS: ['div', 'span', 'p', 'br', 'pre', 'code', 'b', 'strong', 'em', 'i', 'ul', 'ol', 'li',
      'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
      'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'title', 'desc'],
    ALLOWED_ATTR: ['colspan', 'rowspan', 'viewBox', 'width', 'height', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
      'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'fill', 'stroke', 'stroke-width', 'transform', 'xmlns'],
    ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
  })
  // A second, opaque-origin boundary plus CSP protects even sanitizer mistakes.
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"><style>body{font:14px system-ui;color:#1e293b;overflow-wrap:anywhere}table{border-collapse:collapse}td,th{padding:6px;border:1px solid #cbd5e1}svg{max-width:100%}</style></head><body>${clean}</body></html>`
}

// Reject oversized compressed or decoded rasters before assigning a browser URL.
export function rasterData(mime: string, value: string): string | null {
  const encoded = value.replace(/\s/g, '')
  if (encoded.length > 5_600_000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) return null
  let bytes: string
  try { bytes = atob(encoded) } catch { return null }
  const u16 = (i: number) => bytes.charCodeAt(i) * 256 + bytes.charCodeAt(i + 1)
  let width = 0, height = 0
  if (mime === 'image/png' && bytes.slice(0, 8) === '\x89PNG\r\n\x1a\n' && bytes.slice(12, 16) === 'IHDR') {
    width = u16(16) * 65536 + u16(18); height = u16(20) * 65536 + u16(22)
  } else if (mime === 'image/jpeg' && bytes.slice(0, 2) === '\xff\xd8') {
    let i = 2
    while (i + 9 < bytes.length) {
      if (bytes.charCodeAt(i++) !== 255) break
      const marker = bytes.charCodeAt(i++)
      const size = u16(i)
      if ([192, 193, 194].includes(marker)) { height = u16(i + 3); width = u16(i + 5); break }
      if (size < 2 || marker === 218) break
      i += size
    }
  }
  return width > 0 && height > 0 && width <= 8192 && height <= 8192 && width * height <= 16_000_000
    ? `data:${mime};base64,${encoded}` : null
}
