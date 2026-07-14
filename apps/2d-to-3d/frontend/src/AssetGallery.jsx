/**
 * AssetGallery.jsx — the Asset Library: browse, review and pick every 3-D asset.
 *
 * Layout: filter bar (facility + category + search) → responsive grid of cards,
 * each with a live (lazy-mounted) 3-D thumbnail, real-world dimensions and prop
 * key. Click a card → full detail viewer with orbit controls, human-scale
 * reference, debug stats and an "Add to plan" action.
 *
 * Thumbnails are lazy-mounted via IntersectionObserver so we never exceed the
 * browser's WebGL-context limit, and unmount when scrolled far off-screen.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import AssetPreview from './three/AssetPreview'
import { CATALOG, CATEGORIES, catalogByCategory, assetDims } from './three/catalog'
import { makeMats, disposeMats } from './three/materials'

const FACILITY_FILTERS = [
  ['Hospital', 'hospital'], ['Data Center', 'datacenter'], ['All assets', 'all'],
]

/** Lazily mount a thumbnail canvas only while (near) on screen. */
function LazyThumb({ assetKey }) {
  const ref = useRef(null)
  const [show, setShow] = useState(false)
  useEffect(() => {
    const el = ref.current; if (!el) return
    const io = new IntersectionObserver(
      ([e]) => setShow(e.isIntersecting),
      { rootMargin: '120px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return (
    <div className="thumb" ref={ref}>
      {show
        ? <AssetPreview assetKey={assetKey} interactive={false} showHuman showGrid />
        : <div className="thumb-ph">◌</div>}
    </div>
  )
}

function Card({ item, dims, onOpen }) {
  return (
    <div className="acard" onClick={() => onOpen(item)}>
      <LazyThumb assetKey={item.key} />
      <div className="acard-body">
        <div className="acard-title">{item.label}</div>
        <div className="acard-meta">
          <code>{item.key}</code>
          {dims && <span className="dims">{dims.w}×{dims.d}×{dims.h} m</span>}
        </div>
      </div>
    </div>
  )
}

function Detail({ item, dims, onClose, onAdd, canAdd }) {
  return (
    <div className="adetail-backdrop" onClick={onClose}>
      <div className="adetail" onClick={(e) => e.stopPropagation()}>
        <i className="close" onClick={onClose}>×</i>
        <div className="adetail-view">
          <AssetPreview assetKey={item.key} interactive showHuman showGrid background="#0c0f16" />
          <div className="adetail-badge">human ≈ 1.7 m · grid = 1 m</div>
        </div>
        <div className="adetail-info">
          <h3>{item.label}</h3>
          <div className="sub"><code>{item.key}</code> · {item.cat}</div>
          <p className="desc">{item.desc}</p>

          <div className="spec">
            <div className="spec-row"><span>Width</span><b>{dims?.w} m <em>({dims?.wFt} ft)</em></b></div>
            <div className="spec-row"><span>Depth</span><b>{dims?.d} m <em>({dims?.dFt} ft)</em></b></div>
            <div className="spec-row"><span>Height</span><b>{dims?.h} m <em>({dims?.hFt} ft)</em></b></div>
            <div className="spec-row"><span>Facility</span><b>{item.facility.join(', ')}</b></div>
            <div className="spec-row dim"><span>Meshes</span><b>{dims?.meshes}</b></div>
            <div className="spec-row dim"><span>Triangles</span><b>{dims?.tris?.toLocaleString()}</b></div>
          </div>

          <button className="btn primary" disabled={!canAdd} onClick={() => onAdd(item)}>
            {canAdd ? '＋ Add to current plan' : 'Build / load a plan first to add'}
          </button>
          <div className="hint">Drag to orbit · scroll to zoom · auto-rotating</div>
        </div>
      </div>
    </div>
  )
}

export default function AssetGallery({ onAddToPlan, hasScene }) {
  const [facility, setFacility] = useState('hospital')
  const [cat, setCat] = useState('all')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)

  // compute all dimensions once (cheap; builds each prop a single time)
  const dimsMap = useMemo(() => {
    const M = makeMats()
    const map = {}
    for (const it of CATALOG) {
      try { map[it.key] = assetDims(it.key, M) } catch { map[it.key] = null }
    }
    disposeMats(M)
    return map
  }, [])

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return CATALOG.filter((it) => {
      if (facility !== 'all' && !it.facility.includes(facility)) return false
      if (cat !== 'all' && it.cat !== cat) return false
      if (ql && !(`${it.label} ${it.key} ${it.desc}`.toLowerCase().includes(ql))) return false
      return true
    })
  }, [facility, cat, q])

  const groups = useMemo(() => {
    const byCat = {}
    for (const it of filtered) (byCat[it.cat] ||= []).push(it)
    return CATEGORIES.map((c) => ({ cat: c, items: byCat[c] || [] })).filter((g) => g.items.length)
  }, [filtered])

  // category chips relevant to the chosen facility
  const cats = useMemo(() => {
    const set = new Set()
    CATALOG.forEach((it) => { if (facility === 'all' || it.facility.includes(facility)) set.add(it.cat) })
    return CATEGORIES.filter((c) => set.has(c))
  }, [facility])

  return (
    <div className="gallery">
      <div className="gbar">
        <div className="gtitle">Asset Library <span>{filtered.length} of {CATALOG.length}</span></div>
        <div className="gfilters">
          {FACILITY_FILTERS.map(([l, k]) => (
            <div key={k} className={`chip ${facility === k ? 'on' : ''}`}
                 onClick={() => { setFacility(k); setCat('all') }}>{l}</div>
          ))}
        </div>
        <input className="gsearch" placeholder="Search assets…" value={q}
               onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="gcatbar">
        <div className={`tab ${cat === 'all' ? 'on' : ''}`} onClick={() => setCat('all')}>All categories</div>
        {cats.map((c) => (
          <div key={c} className={`tab ${cat === c ? 'on' : ''}`} onClick={() => setCat(c)}>{c}</div>
        ))}
      </div>

      <div className="gscroll">
        {groups.length === 0 && <div className="gempty">No assets match your filter.</div>}
        {groups.map(({ cat: c, items }) => (
          <section key={c} className="gsection">
            <h4 className="ghead">{c} <span>{items.length}</span></h4>
            <div className="ggrid">
              {items.map((it) => (
                <Card key={it.key} item={it} dims={dimsMap[it.key]} onOpen={setOpen} />
              ))}
            </div>
          </section>
        ))}
      </div>

      {open && (
        <Detail item={open} dims={dimsMap[open.key]} onClose={() => setOpen(null)}
                canAdd={hasScene} onAdd={(it) => { onAddToPlan?.(it); setOpen(null) }} />
      )}
    </div>
  )
}
