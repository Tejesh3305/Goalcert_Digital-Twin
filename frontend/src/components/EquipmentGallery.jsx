/**
 * EquipmentGallery — the twin's equipment as a clean asset catalog.
 *
 * ONE card per distinct asset TYPE (not per instance), grouped by catalog
 * category, exactly like the 2d-to-3d Asset Library — each card a real,
 * lazily-mounted 3-D model (AssetPreview). A type's card rolls up the health of
 * all its instances (worst status wins) and shows how many there are; clicking
 * opens the info panel on a representative instance (the least-healthy one) with
 * its live sensor values, condition and asset/warranty info.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { usePolling } from '../hooks/useApi'
import { useEventStream } from '../hooks/useEventStream'
import AssetPreview from '../three/AssetPreview'
import { CATALOG, CATEGORIES } from '../three/catalog'
import EquipmentInfoPanel from './EquipmentInfoPanel'
import api from '../api/client'

const ENTRY = Object.fromEntries(CATALOG.map((c) => [c.key, c]))
const DOT = { crit: '#f43f5e', warn: '#f59e0b', ok: '#5fd08a' }
const RANK = { crit: 2, warn: 1, ok: 0, undefined: 0 }

function deriveStatus(nodes = []) {
  const map = {}
  for (const n of nodes) {
    const fc = n.findings
    if (fc?.critical > 0 || n.severity === 'critical') map[n.id] = 'crit'
    else if (fc?.warning > 0 || n.severity === 'warning') map[n.id] = 'warn'
  }
  return map
}

/** Mount the live 3-D preview only while (near) on-screen. */
function LazyThumb({ propKey }) {
  const ref = useRef(null)
  const [show, setShow] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setShow(e.isIntersecting), { rootMargin: '150px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return (
    <div className="equip-card-thumb" ref={ref}>
      {show
        ? <AssetPreview assetKey={propKey} interactive={false} showHuman={false} showGrid={false} background="#0d1017" />
        : <i className="ti ti-loader" style={{ fontSize: 20, opacity: 0.4 }} />}
    </div>
  )
}

export default function EquipmentGallery({ tenant }) {
  const [scene, setScene] = useState(null)
  const [selected, setSelected] = useState(null) // { node, count, desc }

  useEffect(() => {
    if (!tenant) return
    let alive = true
    api.twinSceneByTenant(tenant)
      .then((r) => { if (alive) setScene(r.scene_result) })
      .catch(() => {})
    return () => { alive = false }
  }, [tenant])

  const { events } = useEventStream(tenant, { max: 20 })
  const { data: topo, refetch } = usePolling(() => api.topology(tenant), 3000, [tenant], { skip: !tenant })
  useEffect(() => { if (events.length) refetch() }, [events.length]) // eslint-disable-line

  const statusMap = useMemo(() => deriveStatus(topo?.nodes), [topo])
  const nodeInfo = useMemo(() => {
    const m = {}
    for (const n of (topo?.nodes || [])) m[n.id] = n
    return m
  }, [topo])

  // Collapse instances → one entry per asset type (prop key).
  const types = useMemo(() => {
    const equip = (scene?.nodes || []).filter((n) => n.kind === 'equipment' && n.entityId)
    const byKey = {}
    for (const n of equip) {
      const key = n.geometry?.prop || 'box'
      const st = statusMap[n.entityId]
      const t = (byKey[key] ||= { key, instances: [], worst: 'ok', rep: n })
      t.instances.push(n)
      if (RANK[st] > RANK[t.worst]) { t.worst = st; t.rep = n } // least-healthy = representative
    }
    return byKey
  }, [scene, statusMap])

  // Group types by catalog category, in catalog order.
  const groups = useMemo(() => {
    const byCat = {}
    for (const t of Object.values(types)) {
      const cat = ENTRY[t.key]?.cat || 'Other'
      ;(byCat[cat] ||= []).push(t)
    }
    for (const k of Object.keys(byCat)) {
      byCat[k].sort((a, b) => (ENTRY[a.key]?.label || a.key).localeCompare(ENTRY[b.key]?.label || b.key))
    }
    const ordered = [...CATEGORIES, 'Other'].filter((c) => byCat[c]?.length)
    return ordered.map((c) => ({ cat: c, items: byCat[c] }))
  }, [types])

  const total = useMemo(() => Object.keys(types).length, [types])

  if (!scene) return <div className="bim-loading"><span className="spinner" /> Loading equipment…</div>
  if (!total) {
    return (
      <div className="bim-loading" style={{ flexDirection: 'column', gap: 6 }}>
        <i className="ti ti-cube-off" style={{ fontSize: 28, opacity: 0.6 }} />
        <div>No equipment to show for this twin yet.</div>
      </div>
    )
  }

  return (
    <div className="equip-gallery">
      <div className="equip-gallery-scroll">
        {groups.map((g) => (
          <section key={g.cat} className="equip-sector">
            <div className="equip-sector-head">
              {g.cat}<span className="equip-sector-count">{g.items.length}</span>
            </div>
            <div className="equip-grid">
              {g.items.map((t) => (
                <TypeCard key={t.key} type={t} active={selected?.node?.geometry?.prop === t.key}
                          onClick={() => setSelected({ node: t.rep, count: t.instances.length, desc: ENTRY[t.key]?.desc })} />
              ))}
            </div>
          </section>
        ))}
      </div>

      {selected && (
        <EquipmentInfoPanel node={selected.node} tenant={tenant} info={nodeInfo[selected.node.entityId]}
                            propKey={selected.node.geometry?.prop} unitCount={selected.count}
                            typeDesc={selected.desc} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

function TypeCard({ type, active, onClick }) {
  const entry = ENTRY[type.key]
  const label = entry?.label || type.key
  const dot = DOT[type.worst] || DOT.ok
  return (
    <button className={`equip-card${active ? ' active' : ''}`} onClick={onClick} title={label}>
      <div className="equip-card-thumb-wrap">
        <LazyThumb propKey={type.key} />
        <span className="equip-card-dot" style={{ background: dot }} />
        {type.instances.length > 1 && <span className="equip-card-count">×{type.instances.length}</span>}
      </div>
      <div className="equip-card-name">{label}</div>
    </button>
  )
}
