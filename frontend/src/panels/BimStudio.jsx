import { useRef, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { useToast } from '../context/ToastContext'
import { useTwin } from '../context/TwinContext'
import { readPlanFile, ACCEPT, kindOf } from '../lib/planUpload'
import BimViewer from '../components/BimViewer'
import api from '../api/client'

/**
 * BIM Studio — drop a 2-D plan, get a living 3-D building.
 *
 * Left: a drop zone + plan preview that feeds the real twin-building graph
 * (Concierge → Plan Parser → Schema Mapper → Validator → Graph Writer → Scene
 * Generator). Right: the interactive 3-D viewer of the reconstructed building.
 *
 * This is the real version of demo.html's faked "Build a Twin" flow: the upload
 * is a real file, the parse is the Plan Parser agent, and the 3-D is generated
 * from the committed building graph.
 */
const TYPES = [
  ['Hospital', 'hospital'],
  ['Smart office', 'office'],
  ['Factory', 'factory'],
  ['Data center', 'datacenter'],
]

export default function BimStudio() {
  const toast = useToast()
  const { refreshTwins, setActiveTenant } = useTwin()

  const [session, setSession] = useState(null)
  const [preview, setPreview] = useState(null) // {dataUrl, filename}
  const [facility, setFacility] = useState('hospital')
  const [floors, setFloors] = useState(3)
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState('idle') // idle | scanning | done
  const [log, setLog] = useState([])
  const [scene, setScene] = useState(null)
  const [tenant, setTenant] = useState(null)
  const fileRef = useRef(null)

  const addLog = (text, cls = '') => setLog((l) => [...l, { text, cls }])

  async function ensureSession() {
    if (session) return session
    const res = await api.twinAgentStart({})
    setSession(res.session_id)
    return res.session_id
  }

  async function handleFile(file) {
    if (!file) return
    setBusy(true)
    try {
      const sid = await ensureSession()
      const { dataUrl, filename } = await readPlanFile(file)
      setPreview({ dataUrl, filename })
      await api.twinAgentUploadData(sid, dataUrl, filename)
      addLog(`Plan loaded: ${filename}`, 'ok')
      toast.ok('Plan loaded', filename)
    } catch (e) {
      toast.err('Could not load plan', e.message)
    } finally {
      setBusy(false)
    }
  }

  function onDrop(e) {
    e.preventDefault()
    const f = e.dataTransfer.files?.[0]
    if (f) handleFile(f)
  }

  async function build() {
    if (!preview) { toast.err('Add a plan first', 'Drop or pick a 2-D floor plan.'); return }
    setBusy(true)
    setPhase('scanning')
    setLog([])
    try {
      const sid = await ensureSession()
      addLog('Vectorising plan → walls, doors, zones', 'acc')
      // One message drives the whole graph: Concierge → Plan Parser (vision) →
      // Classifier → Composer → Schema Mapper → Validator → Graph Writer → Scene.
      const msg = `Build a ${facility} twin from the uploaded floor plan. ` +
        `It has ${floors} floor(s). Monitor HVAC, power and safety.`
      const res = await api.twinAgentMessage(sid, msg)
      const st = res.state || {}

      if (st.bim_model) {
        const rooms = (st.bim_model.rooms || []).length
        addLog(`Detected ${rooms} rooms · ${(st.bim_model.equipment || []).length} assets`, 'ok')
      }
      if (st.validation) addLog('Validating against SHACL shapes … ' + (st.validation.ok ? 'passed' : 'failed'), st.validation.ok ? 'ok' : 'crit')

      if (!st.committed || !st.twin_id) {
        addLog(st.reply_to_user || 'Could not commit the twin.', 'crit')
        toast.err('Build incomplete', st.reply_to_user || 'The agent needs more detail.')
        setPhase('idle')
        return
      }

      addLog('Extruding geometry · placing equipment', '')
      // Scene may already be on the committed state; otherwise request it.
      let sc = st.scene_result
      if (!sc || sc.status === 'skipped' || sc.format !== 'nxr-scene/1') {
        const sres = await api.twinAgentScene(sid)
        sc = sres.scene_result || sres.state?.scene_result || sres
      }
      setScene(sc)
      setTenant(st.twin_id)
      setActiveTenant(st.twin_id)
      await refreshTwins()
      addLog('Twin ready — opening 3-D', 'ok')
      setPhase('done')
      toast.ok('Twin built', `${st.twin_name || st.twin_id} is live`)
    } catch (e) {
      addLog('Build error: ' + e.message, 'crit')
      toast.err('Build failed', e.message)
      setPhase('idle')
    } finally {
      setBusy(false)
    }
  }

  function reset() {
    setPreview(null); setScene(null); setTenant(null); setPhase('idle'); setLog([])
  }

  return (
    <div className="panel">
      <PanelHeader
        title="BIM Studio"
        subtitle="Drop a 2-D plan, get a living 3-D building — parsed, validated, and reconstructed."
      >
        {(preview || scene) && (
          <button className="btn" onClick={reset}><i className="ti ti-refresh" /> New plan</button>
        )}
      </PanelHeader>

      <div style={{ display: 'grid', gridTemplateColumns: scene ? '360px 1fr' : '1fr 1fr', gap: 12 }}>
        {/* ── Left: upload + controls ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card title="1 · Your floor plan">
            <div
              className="bim-dropzone"
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault() }}
              onDrop={onDrop}
            >
              {preview
                ? <img src={preview.dataUrl} alt={preview.filename} className="bim-preview" />
                : (
                  <div className="bim-dz-empty">
                    <i className="ti ti-cloud-upload" style={{ fontSize: 30, color: 'var(--accent-blue)' }} />
                    <div style={{ fontWeight: 600, marginTop: 6 }}>Drag &amp; drop your 2-D plan</div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>PNG · JPG · PDF · or click to browse</div>
                    <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>IFC / DXF accepted (true-BIM fast-follow)</div>
                  </div>
                )}
              <input ref={fileRef} type="file" accept={ACCEPT} style={{ display: 'none' }}
                     onChange={(e) => handleFile(e.target.files?.[0])} />
            </div>
            {preview && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{kindOf(preview.filename) === 'bim' ? 'BIM model attached' : 'Image attached'} · {preview.filename}</div>}
          </Card>

          <Card title="2 · Building details">
            <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>Facility type</div>
            <div className="chat-quick" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {TYPES.map(([label, key]) => (
                <div key={key}
                     className={`quick-chip ${facility === key ? 'active' : ''}`}
                     onClick={() => setFacility(key)}
                     style={facility === key ? { borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' } : undefined}>
                  {label}
                </div>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 11, margin: '10px 0 6px' }}>Floors</div>
            <input className="input" type="number" min={1} max={20} value={floors}
                   onChange={(e) => setFloors(Math.max(1, Math.min(20, +e.target.value || 1)))} />
            <button className="btn btn-primary" style={{ marginTop: 12, width: '100%' }}
                    onClick={build} disabled={busy || !preview}>
              {busy ? <><span className="spinner" /> Building…</> : <><i className="ti ti-wand" /> Analyze &amp; Build 3-D Twin</>}
            </button>
          </Card>

          {log.length > 0 && (
            <Card title="Build log">
              <div className="scan-log" style={{ maxHeight: 180, overflow: 'auto', fontFamily: 'var(--mono)', fontSize: 11 }}>
                {log.map((l, i) => (
                  <div key={i} style={{ color: l.cls === 'ok' ? 'var(--accent-green)' : l.cls === 'crit' ? 'var(--accent-red)' : l.cls === 'acc' ? 'var(--accent-blue)' : 'var(--muted)' }}>
                    {(l.cls === 'ok' ? '✓ ' : '> ') + l.text}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>

        {/* ── Right: 3-D viewer ── */}
        <Card title={scene ? 'Interactive 3-D twin' : 'Reconstructed building'} style={{ padding: scene ? 0 : undefined, overflow: 'hidden' }}>
          {scene
            ? <BimViewer scene={scene} tenant={tenant} />
            : (
              <div className="bim-viewer-empty">
                <i className="ti ti-cube" style={{ fontSize: 40, color: 'var(--accent-blue)', opacity: 0.5 }} />
                <div style={{ marginTop: 10, fontWeight: 600 }}>Your 3-D building appears here</div>
                <div className="muted" style={{ fontSize: 12, marginTop: 4, maxWidth: 340, textAlign: 'center' }}>
                  Drop a floor plan and press <b>Build</b>. The platform parses it into a Building→Floor→Room graph, then reconstructs it in 3-D.
                </div>
              </div>
            )}
        </Card>
      </div>
    </div>
  )
}
