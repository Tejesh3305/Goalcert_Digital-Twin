/** Small formatting + presentation helpers shared across panels. */

export const shortId = (id, n = 8) => (id ? `${String(id).slice(0, n)}…` : '—')

export const timeOf = (iso) => {
  if (!iso) return ''
  try { return new Date(iso).toLocaleTimeString() } catch { return iso }
}

export const dateOf = (iso) => {
  if (!iso) return ''
  try { return new Date(iso).toLocaleDateString() } catch { return iso }
}

// Icon + accent per taxonomy category / Neo4j label.
export const LABEL_META = {
  PhysicalAsset: { icon: 'ti-cpu', color: 'var(--accent-blue)' },
  Location:      { icon: 'ti-map-pin', color: 'var(--accent-teal)' },
  Finding:       { icon: 'ti-alert-triangle', color: 'var(--accent-amber)' },
  Incident:      { icon: 'ti-urgent', color: 'var(--accent-red)' },
  Process:       { icon: 'ti-refresh', color: 'var(--accent-purple)' },
  Observation:   { icon: 'ti-eye', color: 'var(--accent-teal)' },
  Capability:    { icon: 'ti-puzzle', color: 'var(--accent-green)' },
  Document:      { icon: 'ti-file-text', color: 'var(--muted)' },
  Actor:         { icon: 'ti-user', color: 'var(--accent-blue)' },
  MobileAsset:   { icon: 'ti-truck', color: 'var(--accent-blue)' },
}

export const labelMeta = (label) =>
  LABEL_META[label] || { icon: 'ti-circle', color: 'var(--muted)' }

// The local name of a canonical IRI ("…#AirHandler" -> "AirHandler").
export const localName = (iri) =>
  iri ? String(iri).split('#').pop().split('/').pop() : ''

export const actionColor = (action) => ({
  create: 'var(--accent-green)',
  update: 'var(--accent-blue)',
  delete: 'var(--accent-red)',
}[action] || 'var(--muted)')
