import { useEffect, useRef, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { useToast } from '../context/ToastContext'
import api from '../api/client'

/**
 * Accelerator Pack Composer — Team 4: Platform Extension.
 *
 * Chat to select bundles, adapters, and compliance docs, then assemble them
 * into a Solution Accelerator Pack (e.g. the Maritime SAP, HVAC SAP).
 *
 *   Pack Interviewer → Bundle Selector → Pack Assembler → END
 */
const STAGES = [
  { key: 'interview', label: 'Interviewer', icon: 'ti-microphone' },
  { key: 'select', label: 'Bundle Selector', icon: 'ti-list-check' },
  { key: 'assemble', label: 'Assembler', icon: 'ti-package' },
]

export default function AcceleratorPack() {
  const toast = useToast()
  const [session, setSession] = useState(null)
  const [state, setState] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const start = async () => {
    setBusy(true)
    try {
      const res = await api.accelStart()
      setSession(res.session_id)
      setState(res.state)
      setMessages([{ role: 'ai', text: res.state.reply_to_user || 'What domain should this accelerator pack target?' }])
    } catch (e) { toast.err('Could not start', e.message) } finally { setBusy(false) }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || !session) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.accelMessage(session, text)
      setState(res.state)
      if (res.state.reply_to_user) setMessages((m) => [...m, { role: 'ai', text: res.state.reply_to_user }])
    } catch (e) { toast.err('Agent error', e.message) } finally { setBusy(false) }
  }

  const deriveStage = () => {
    if (!state) return -1
    if (state.pack_manifest) return 2
    if (state.selected_bundles?.length) return 1
    return 0
  }
  const stage = deriveStage()

  // Init screen
  if (!session) return (
    <div className="panel">
      <PanelHeader title="Accelerator Packs" subtitle="Assemble a Solution Accelerator Pack from bundles" />
      <Card>
        <p style={{ color: 'var(--text-dim)', marginBottom: 14, fontSize: 14 }}>
          Combine multiple capability bundles, reference adapters, and compliance docs into a
          single tender-ready Solution Accelerator Pack. The interviewer will ask about your target
          domain, then the selector picks bundles and the assembler produces the manifest.
        </p>
        <button className="btn btn-primary" onClick={start} disabled={busy}>
          {busy ? 'Starting...' : 'Start Accelerator Session'}
        </button>
      </Card>
    </div>
  )

  return (
    <div className="panel">
      <PanelHeader title="Accelerator Packs" subtitle="Assemble a Solution Accelerator Pack" />

      {/* Pipeline rail */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 18 }}>
        {STAGES.map((s, i) => (
          <div key={s.key} style={{
            padding: '4px 12px', borderRadius: 20, fontSize: 12, fontFamily: 'var(--mono)',
            background: i <= stage ? 'var(--accent)' : 'var(--bg-card)',
            color: i <= stage ? '#fff' : 'var(--text-dim)',
            border: '1px solid ' + (i <= stage ? 'var(--accent)' : 'var(--border)'),
          }}>
            <i className={s.icon} style={{ marginRight: 4 }} />{s.label}
          </div>
        ))}
      </div>

      {/* Chat */}
      <Card>
        <div style={{ maxHeight: 320, overflowY: 'auto', marginBottom: 10 }}>
          {messages.map((m, i) => (
            <div key={i} style={{
              textAlign: m.role === 'user' ? 'right' : 'left',
              margin: '6px 0',
            }}>
              <span style={{
                display: 'inline-block', padding: '6px 12px', borderRadius: 12,
                background: m.role === 'user' ? 'var(--accent)' : 'var(--bg-card)',
                color: m.role === 'user' ? '#fff' : 'var(--text)',
                maxWidth: '80%', textAlign: 'left', fontSize: 14,
                border: m.role === 'user' ? 'none' : '1px solid var(--border)',
              }}>{m.text}</span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
        {state?.awaiting_input && (
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="input" style={{ flex: 1 }} value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && send()}
              placeholder="Describe the accelerator pack..." disabled={busy} />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
              Send
            </button>
          </div>
        )}
      </Card>

      {/* Selected bundles */}
      {state?.selected_bundles?.length > 0 && (
        <Card style={{ marginTop: 14 }}>
          <h4 style={{ margin: '0 0 8px' }}>
            <i className="ti-list-check" style={{ marginRight: 6 }} />Selected Bundles
          </h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {state.selected_bundles.map((b, i) => (
              <span key={i} style={{
                padding: '3px 10px', borderRadius: 16, fontSize: 12,
                fontFamily: 'var(--mono)', background: 'var(--bg-card)',
                border: '1px solid var(--border)',
              }}>{b}</span>
            ))}
          </div>
        </Card>
      )}

      {/* Adapters */}
      {state?.adapters?.length > 0 && (
        <Card style={{ marginTop: 10 }}>
          <h4 style={{ margin: '0 0 8px' }}>
            <i className="ti-plug" style={{ marginRight: 6 }} />Adapters
          </h4>
          {state.adapters.map((a, i) => (
            <div key={i} style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 4 }}>
              <strong>{a.name}</strong> ({a.type})
            </div>
          ))}
        </Card>
      )}

      {/* Pack manifest */}
      {state?.pack_manifest && (
        <Card style={{ marginTop: 14, borderColor: 'var(--green, #22c55e)' }}>
          <h4 style={{ margin: '0 0 10px', color: 'var(--green, #22c55e)' }}>
            <i className="ti-package" style={{ marginRight: 6 }} />
            {state.pack_manifest.pack_name}
          </h4>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            <div>Domain: <strong>{state.pack_manifest.domain}</strong></div>
            <div>Version: {state.pack_manifest.version}</div>
            <div>{state.pack_manifest.metadata?.total_bundles} bundle(s), {state.pack_manifest.metadata?.total_adapters} adapter(s), {state.pack_manifest.metadata?.total_compliance_docs} compliance doc(s)</div>
          </div>
          {state.compliance_docs?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text-dim)', marginBottom: 4 }}>
                Compliance
              </div>
              {state.compliance_docs.map((d, i) => (
                <div key={i} style={{ fontSize: 13, marginBottom: 2 }}>
                  {d.title} ({d.standard})
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
