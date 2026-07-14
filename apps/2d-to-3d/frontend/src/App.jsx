import { useRef, useState } from 'react'
import Viewer from './Viewer'
import AssetGallery from './AssetGallery'
import { readPlanFile, ACCEPT } from './lib/planUpload'

const FACILITIES = [
  ['Residential', 'residential'], ['Hospital', 'hospital'],
  ['Data Center', 'datacenter'], ['Office', 'office'], ['Factory', 'factory'],
]

export default function App() {
  const [mode, setMode] = useState('build')      // 'build' | 'library'
  const [scene, setScene] = useState(null)
  const [preview, setPreview] = useState(null)
  const [facility, setFacility] = useState('residential')
  const [floors, setFloors] = useState(1)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const addCount = useRef(0)
  const fileRef = useRef(null)

  async function pickFile(file) {
    if (!file) return
    try {
      const { dataUrl, filename } = await readPlanFile(file)
      setPreview({ dataUrl, filename })
      setNote('')
    } catch (e) { setNote('Could not read file: ' + e.message) }
  }

  async function build() {
    if (!preview) { setNote('Add a plan image/PDF first (or load a sample).'); return }
    setBusy(true); setNote('Parsing plan with the vision model…')
    try {
      const r = await fetch('/api/parse', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: preview.dataUrl, filename: preview.filename, facility, floors }),
      })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'parse failed')
      setScene(j.scene); addCount.current = 0
      const s = j.scene
      setNote(s.synthesized
        ? `⚠ Vision parse fell back to a generic ${s.facility} layout — ${s.parse_note || 'check API key/log'}.`
        : `✓ Parsed with ${s.vision_backend || 'vision'} · facility: ${s.facility} · ${s.nodes.length} elements.`)
    } catch (e) { setNote('Error: ' + e.message) } finally { setBusy(false) }
  }

  async function loadSample(f) {
    setBusy(true); setNote(`Loading sample ${f} twin…`)
    try {
      const r = await fetch(`/api/sample/${f}?floors=${floors}`)
      const j = await r.json()
      setScene(j.scene); addCount.current = 0; setNote(`Sample ${f} twin (no LLM needed).`)
    } catch (e) { setNote('Error: ' + e.message) } finally { setBusy(false) }
  }

  // Inject a library asset into the current scene, placed in a tidy grid near
  // the building centre at ground level.
  function addToPlan(item) {
    setScene((cur) => {
      if (!cur) return cur
      const bb = cur.bbox || { min: [0, 0, 0], max: [0, 0, 0] }
      const cx = (bb.min[0] + bb.max[0]) / 2
      const cz = (bb.min[2] + bb.max[2]) / 2
      const y = cur.levels?.[0]?.elevationM ?? 0
      const n = addCount.current++
      const col = n % 5, row = Math.floor(n / 5)
      const node = {
        id: `lib-${item.key}-${n}`, entityId: null, kind: 'equipment',
        type: 'library', label: item.label, level: cur.levels?.[0]?.index ?? 0,
        transform: { pos: [cx + (col - 2) * 2.4, y, cz + row * 2.4], rotY: 0, scale: [1, 1, 1] },
        geometry: { kind: 'prop', prop: item.key }, decor: false, status: null,
      }
      return { ...cur, nodes: [...cur.nodes, node] }
    })
    setMode('build')
    setNote(`＋ Added “${item.label}” to the plan (centre, ground floor). Orbit to find it.`)
  }

  return (
    <div className="app">
      <aside className="panel">
        <div className="brand">NextXR · <b>2-D → 3-D</b></div>

        <div className="tabs">
          <div className={`tab ${mode === 'build' ? 'on' : ''}`} onClick={() => setMode('build')}>Build from plan</div>
          <div className={`tab ${mode === 'library' ? 'on' : ''}`} onClick={() => setMode('library')}>Asset Library</div>
        </div>

        {mode === 'build' ? (
          <>
            <p className="muted">Drop a floor-plan image or PDF and reconstruct it as a navigable, furnished 3-D model.</p>

            <div className="drop" onClick={() => fileRef.current?.click()}
                 onDragOver={(e) => e.preventDefault()}
                 onDrop={(e) => { e.preventDefault(); pickFile(e.dataTransfer.files?.[0]) }}>
              {preview ? <img src={preview.dataUrl} alt="" /> : (
                <div className="dz"><div className="big">⤓</div>Drag &amp; drop your plan<br /><span>PNG · JPG · PDF</span></div>
              )}
              <input ref={fileRef} type="file" accept={ACCEPT} hidden
                     onChange={(e) => pickFile(e.target.files?.[0])} />
            </div>

            <label className="lbl">Facility type</label>
            <div className="chips">
              {FACILITIES.map(([l, k]) => (
                <div key={k} className={`chip ${facility === k ? 'on' : ''}`} onClick={() => setFacility(k)}>{l}</div>
              ))}
            </div>
            <label className="lbl">Floors</label>
            <input className="num" type="number" min={1} max={12} value={floors}
                   onChange={(e) => setFloors(Math.max(1, Math.min(12, +e.target.value || 1)))} />

            <button className="btn primary" disabled={busy || !preview} onClick={build}>
              {busy ? 'Working…' : 'Build 3-D'}
            </button>
            <div className="muted small">Or load a sample (no API key needed):</div>
            <div className="chips">
              {FACILITIES.map(([l, k]) => (
                <div key={k} className="chip" onClick={() => loadSample(k)}>{l}</div>
              ))}
            </div>
            {note && <div className="note">{note}</div>}
          </>
        ) : (
          <>
            <p className="muted">Browse every realistic 3-D asset on its own. Review proportions against the 1.7 m
              human and 1 m grid, then add any asset straight into your current plan.</p>
            <div className="note">
              {scene ? '✓ A plan is loaded — open any asset and press “Add to plan”.'
                     : 'Tip: build or load a sample plan first, then you can add assets into it.'}
            </div>
          </>
        )}
      </aside>

      <main className="stage">
        {mode === 'library'
          ? <AssetGallery onAddToPlan={addToPlan} hasScene={!!scene} />
          : scene ? <Viewer scene={scene} />
            : <div className="placeholder"><div className="big">🏠</div>Your 3-D model appears here.<br />
                <span>Drop a plan &amp; press Build, load a sample, or open the Asset Library.</span></div>}
      </main>
    </div>
  )
}
