/**
 * Markdown — a tiny renderer for the agents' report output.
 *
 * The reporting agents (diagnosis, analysis, cascade) return markdown with
 * headings, bold, bullets and the occasional table row. This covers exactly
 * that subset. It is deliberately NOT a general markdown engine and pulls in no
 * dependency — the frontend ships no markdown library, and adding one to render
 * four constructs would be the wrong trade.
 *
 * Everything is rendered as text nodes (no dangerouslySetInnerHTML), so model
 * output cannot inject markup.
 */

// Inline: **bold**, `code`. Split on the tokens and rebuild as React nodes.
function inline(text, keyPrefix = 'i') {
  const parts = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) {
      parts.push(<strong key={`${keyPrefix}-b${i}`}>{tok.slice(2, -2)}</strong>)
    } else {
      parts.push(<code key={`${keyPrefix}-c${i}`} className="md-code">{tok.slice(1, -1)}</code>)
    }
    last = m.index + tok.length
    i += 1
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

export default function Markdown({ children, className = '' }) {
  const src = String(children || '').trim()
  if (!src) return null

  const blocks = []
  const lines = src.split('\n')
  let list = null      // accumulating <li> items
  let ordered = false

  const flushList = () => {
    if (!list) return
    const Tag = ordered ? 'ol' : 'ul'
    blocks.push(<Tag key={`l${blocks.length}`} className="md-list">{list}</Tag>)
    list = null
  }

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd()
    const t = line.trim()

    if (!t) { flushList(); return }

    // Horizontal rule
    if (/^([-*_])\1{2,}$/.test(t)) {
      flushList()
      blocks.push(<hr key={`h${idx}`} className="md-hr" />)
      return
    }

    // Headings — ###### down to #
    const h = /^(#{1,6})\s+(.*)$/.exec(t)
    if (h) {
      flushList()
      const level = Math.min(h[1].length, 6)
      const Tag = `h${Math.min(level + 2, 6)}`   // #→h3, so it sits under the card title
      blocks.push(<Tag key={`h${idx}`} className={`md-h md-h${level}`}>{inline(h[2], `h${idx}`)}</Tag>)
      return
    }

    // Ordered list
    const ol = /^(\d+)[.)]\s+(.*)$/.exec(t)
    if (ol) {
      if (!list || !ordered) { flushList(); list = []; ordered = true }
      list.push(<li key={`li${idx}`}>{inline(ol[2], `o${idx}`)}</li>)
      return
    }

    // Bullet list
    const ul = /^[-*•]\s+(.*)$/.exec(t)
    if (ul) {
      if (!list || ordered) { flushList(); list = []; ordered = false }
      list.push(<li key={`li${idx}`}>{inline(ul[1], `u${idx}`)}</li>)
      return
    }

    flushList()
    blocks.push(<p key={`p${idx}`} className="md-p">{inline(t, `p${idx}`)}</p>)
  })
  flushList()

  return <div className={`md ${className}`}>{blocks}</div>
}
