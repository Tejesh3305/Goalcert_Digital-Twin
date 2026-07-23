// CascadeGraph.jsx — visual SVG dependency graph showing how faults propagate
// between subsystems. Nodes are colored by health, edges pulse along the
// propagation direction. Used in the Dashboard to replace plain-text cascade output.
import React, { useMemo, useState } from 'react'
import { Icon } from './lib.jsx'

// Parse a cascade text into a structured graph. The text typically contains
// lines like "Degradation in X propagates to Y" or "X → Y". We extract
// subsystem names and build edges. If parsing fails, fall back to a linear chain.
function parseCascade(text, findings = []) {
  if (!text) return { nodes: [], edges: [] }

  // extract system names from findings
  const systemNames = new Set()
  findings.forEach(f => {
    const name = f.displayName || f.behaviorId || ''
    const match = name.match(/^(?:\[.\]\s*)?(.+?)(?:\s*—|\s*:)/)?.[1]
    if (match) systemNames.add(match.trim())
  })

  // try to parse structured edges from text
  const edges = []
  const nodeSet = new Set()

  // pattern: "X → Y", "X -> Y", "X propagates to Y", "X can lead to Y"
  const patterns = [
    /(\w[\w\s&/]+?)\s*(?:→|->|propagates?\s+to|leads?\s+to|affects?|impacts?|degrades?)\s*(\w[\w\s&/]+?)(?:\.|,|;|\n|$)/gi,
  ]

  for (const pat of patterns) {
    let m
    while ((m = pat.exec(text)) !== null) {
      const from = m[1].trim().replace(/\s+/g, ' ')
      const to = m[2].trim().replace(/\s+/g, ' ')
      if (from.length > 2 && to.length > 2 && from !== to) {
        nodeSet.add(from); nodeSet.add(to)
        edges.push({ from, to })
      }
    }
  }

  // if we couldn't parse any edges, build from findings
  if (edges.length === 0 && findings.length > 0) {
    const fNames = findings.map(f => {
      const dn = f.displayName || f.behaviorId || 'Unknown'
      return dn.replace(/\[.\]\s*/, '').split('—')[0].trim()
    }).filter(Boolean)
    const unique = [...new Set(fNames)].slice(0, 5)
    unique.forEach(n => nodeSet.add(n))
    for (let i = 0; i < unique.length - 1; i++) {
      edges.push({ from: unique[i], to: unique[i + 1] })
    }
  }

  const nodes = [...nodeSet].map((name, i) => ({
    id: name,
    label: name.length > 20 ? name.slice(0, 18) + '…' : name,
    severity: findings.find(f => (f.displayName || '').includes(name))?.severity || 'nominal',
  }))

  return { nodes, edges }
}

// Simple force-free layout: arrange nodes in a horizontal arc
function layoutNodes(nodes, width, height) {
  const cx = width / 2, cy = height / 2
  const n = nodes.length
  if (n === 0) return []
  if (n === 1) return [{ ...nodes[0], x: cx, y: cy }]

  const radiusX = (width - 120) / 2
  const radiusY = (height - 80) / 2

  return nodes.map((node, i) => {
    const angle = Math.PI + (Math.PI * i) / (n - 1) // bottom arc
    return {
      ...node,
      x: cx + radiusX * Math.cos(angle),
      y: cy + radiusY * Math.sin(angle) * 0.6 + 10,
    }
  })
}

const SEV_COLORS = {
  critical: '#e11d48',
  warning: '#d97706',
  nominal: '#16a34a',
}

export default function CascadeGraph({ text, findings = [], height = 220 }) {
  const [hovered, setHovered] = useState(null)
  const W = 600

  const { nodes, edges } = useMemo(() => parseCascade(text, findings), [text, findings])
  const laid = useMemo(() => layoutNodes(nodes, W, height), [nodes, W, height])

  if (nodes.length === 0) {
    return (
      <div style={{ borderLeft: '3px solid var(--brand-2)', padding: '14px 16px',
        background: 'var(--brand-softer)', borderRadius: '0 12px 12px 0',
        fontSize: 13, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
        {text || 'No cascade data available.'}
      </div>
    )
  }

  const nodeMap = Object.fromEntries(laid.map(n => [n.id, n]))

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" style={{ display: 'block' }}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,4 L0,8 Z" fill="#7c3aed" opacity="0.6" />
          </marker>
          {/* animated dash for edge pulse */}
          <style>{`
            @keyframes edgePulse { to { stroke-dashoffset: -20; } }
            .cg-edge { stroke-dasharray: 8 12; animation: edgePulse 1.2s linear infinite; }
            .cg-node { transition: r .15s ease, filter .15s; }
            .cg-node:hover { filter: drop-shadow(0 0 8px currentColor); }
          `}</style>
        </defs>

        {/* edges */}
        {edges.map((e, i) => {
          const from = nodeMap[e.from], to = nodeMap[e.to]
          if (!from || !to) return null
          const isHighlight = hovered === e.from || hovered === e.to
          return (
            <line key={i} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
              stroke={isHighlight ? '#7c3aed' : '#9aa1ad'} strokeWidth={isHighlight ? 2.5 : 1.5}
              className="cg-edge" markerEnd="url(#arrow)" opacity={isHighlight ? 1 : 0.5} />
          )
        })}

        {/* nodes */}
        {laid.map(n => {
          const color = SEV_COLORS[n.severity] || SEV_COLORS.nominal
          const isHover = hovered === n.id
          return (
            <g key={n.id} onMouseEnter={() => setHovered(n.id)} onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'pointer' }}>
              {/* glow ring */}
              <circle cx={n.x} cy={n.y} r={isHover ? 28 : 24} fill="none" stroke={color}
                strokeWidth="2" opacity={isHover ? 0.4 : 0.15} />
              {/* node circle */}
              <circle cx={n.x} cy={n.y} r={isHover ? 20 : 17} fill={color} opacity={0.9}
                className="cg-node" style={{ color }} />
              {/* label */}
              <text x={n.x} y={n.y + 32} textAnchor="middle" fill="var(--text)"
                fontSize="10" fontWeight="600" fontFamily="var(--font)">
                {n.label}
              </text>
              {/* severity badge inside */}
              <text x={n.x} y={n.y + 4} textAnchor="middle" fill="#fff"
                fontSize="8" fontWeight="700" fontFamily="var(--mono)">
                {n.severity === 'critical' ? 'CRIT' : n.severity === 'warning' ? 'WARN' : 'OK'}
              </text>
            </g>
          )
        })}
      </svg>

      {/* fallback text below the graph */}
      {text && (
        <details style={{ marginTop: 8, fontSize: 12, color: 'var(--muted)' }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: 11 }}>
            <Icon n="ti-text-wrap" /> Full analysis text
          </summary>
          <div style={{ marginTop: 6, lineHeight: 1.7, whiteSpace: 'pre-wrap', padding: '8px 12px',
            background: 'var(--surface2)', borderRadius: 10 }}>
            {text}
          </div>
        </details>
      )}
    </div>
  )
}
