import { useEffect, useRef, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { useToast } from '../context/ToastContext'
import api from '../api/client'

/**
 * Bundle Author — the meta-agent (the demo's star).
 *
 * Chat with the Interviewer to define a new vertical. Behind it:
 *   Interviewer → Ontology Drafter → Rule Author → Linter → [HUMAN GATE] → Publisher
 * When lint passes, the drafted Turtle + Tier-C rule appear with an Approve
 * button (the non-negotiable human gate). Approving publishes a real bundle
 * the Concierge flow can immediately build twins from — closing the loop.
 */
const STAGES = [
  { key: 'interview', label: 'Interviewer', icon: 'ti-microphone' },
  { key: 'draft', label: 'Ontology Drafter', icon: 'ti-file-code' },
  { key: 'model', label: 'Behavior Modeler', icon: 'ti-brain' },
  { key: 'rules', label: 'Rule Author', icon: 'ti-ruler' },
  { key: 'elicit', label: 'Elicitation Designer', icon: 'ti-help-circle' },
  { key: 'curate', label: 'Asset Curator', icon: 'ti-cube' },
  { key: 'lint', label: 'Linter', icon: 'ti-checks' },
  { key: 'await_approval', label: 'Human Gate', icon: 'ti-user-check' },
  { key: 'publish', label: 'Publisher', icon: 'ti-package' },
]

export default function BundleAuthor() {
  const toast = useToast()
  const [session, setSession] = useState(null)
  const [domain, setDomain] = useState('')
  const [bundleName, setBundleName] = useState('')
  const [state, setState] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const start = async () => {
    if (!domain.trim()) { toast.err('Domain required', 'Name the vertical, e.g. “refrigeration”.'); return }
    setBusy(true)
    try {
      const res = await api.bundleStart({ domain: domain.trim(), bundle_name: bundleName.trim() || undefined })
      setSession(res.session_id)
      setState(res.state)
      setMessages([{ role: 'ai', text: res.state.reply_to_user || 'Tell me about this vertical — its key assets, measurements, and faults.' }])
    } catch (e) { toast.err('Could not start', e.message) } finally { setBusy(false) }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || !session) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.bundleMessage(session, text)
      setState(res.state)
      if (res.state.reply_to_user) setMessages((m) => [...m, { role: 'ai', text: res.state.reply_to_user }])
    } catch (e) { toast.err('Agent error', e.message) } finally { setBusy(false) }
  }

  const approve = async () => {
    setBusy(true)
    try {
      const res = await api.bundleApprove(session)
      setState(res.state)
      if (res.state.published_bundle) {
        toast.ok('Bundle published', `${res.state.published_bundle} is now in the registry`)
        setMessages((m) => [...m, { role: 'ai', text: res.state.reply_to_user }])
      }
    } catch (e) { toast.err('Approve failed', e.message) } finally { setBusy(false) }
  }

  const stage = deriveStage(state)
  const lintOk = state?.lint_result?.ok
  const awaitingApproval = lintOk && !state?.published_bundle

  return (
    <div className="panel">
      <PanelHeader
        title="Bundle Author"
        subtitle="Author a brand-new vertical. A human approves before it's published — then the Concierge can build twins from it."
      >
        {session && (
          <button className="btn" onClick={() => { setSession(null); setState(null); setMessages([]) }}>
            <i className="ti ti-refresh" /> New session
          </button>
        )}
      </PanelHeader>

      {!session ? (
        <Card title="Start a new bundle">
          <div className="field">
            <label>Vertical / domain</label>
            <input className="input" value={domain} placeholder="e.g. refrigeration, solar, water-treatment"
                   onChange={(e) => setDomain(e.target.value)} />
          </div>
          <div className="field">
            <label>Bundle name (optional)</label>
            <input className="input" value={bundleName} placeholder="e.g. Cold Storage Pack"
                   onChange={(e) => setBundleName(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={start} disabled={busy}>
            <i className="ti ti-wand" /> Begin authoring
          </button>
        </Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 12 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Card>
              <div className="chat-wrap" style={{ height: 320 }}>
                <div className="chat-messages">
                  {messages.map((m, i) => (
                    <div key={i} className={`chat-bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-ai'}`}>
                      {m.role === 'ai' && <div className="bubble-label">Bundle Author</div>}
                      {m.text}
                    </div>
                  ))}
                  {busy && <div className="chat-bubble bubble-ai"><span className="spinner" /> working…</div>}
                  <div ref={endRef} />
                </div>
                <div className="chat-input-row">
                  <input className="input" value={input} placeholder="Describe assets, measurements, faults…"
                         disabled={busy || !!state?.published_bundle}
                         onChange={(e) => setInput(e.target.value)}
                         onKeyDown={(e) => e.key === 'Enter' && send()} />
                  <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
                    <i className="ti ti-send" />
                  </button>
                </div>
              </div>
            </Card>

            {/* Drafted artifacts + the human gate */}
            {state?.ontology_fragment && (
              <Card title={<><i className="ti ti-file-code" /> Drafted Ontology Fragment</>}>
                <pre style={{ fontFamily: 'var(--mono)', fontSize: 10.5, lineHeight: 1.5,
                              background: 'var(--surface2)', padding: 10, borderRadius: 6,
                              maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                  {state.ontology_fragment}
                </pre>
              </Card>
            )}

            {state?.behavior_models?.length > 0 && (
              <Card title={<><i className="ti ti-brain" /> Behavior Models</>}>
                {state.behavior_models.map((m, i) => (
                  <div key={i} className="event-item" style={{ marginBottom: 6 }}>
                    <div className="event-icon" style={{ background: m.tier === 'A' ? 'var(--accent-blue)' : m.tier === 'B' ? 'var(--accent-amber)' : 'var(--accent-green)', color: '#fff' }}>
                      <span style={{ fontSize: 10, fontWeight: 700 }}>{m.tier}</span>
                    </div>
                    <div className="event-body">
                      <div className="event-title">{m.fault} <span className="pill pill-surface">Tier {m.tier} — {m.artefact_type}</span></div>
                    </div>
                  </div>
                ))}
              </Card>
            )}

            {state?.rules?.length > 0 && (
              <Card title={<><i className="ti ti-ruler" /> Authored Rules (Tier-C)</>}>
                {state.rules.map((r, i) => (
                  <div key={i} className="event-item" style={{ marginBottom: 6 }}>
                    <div className="event-icon ev-warn"><i className="ti ti-flame" /></div>
                    <div className="event-body">
                      <div className="event-title">{r.behavior_id} <span className="pill pill-surface">Tier {r.tier}</span></div>
                      <div className="event-meta">{r.description}</div>
                    </div>
                  </div>
                ))}
              </Card>
            )}

            {state?.elicitation_questions?.length > 0 && (
              <Card title={<><i className="ti ti-help-circle" /> Elicitation Questions</>}>
                {state.elicitation_questions.map((q, i) => (
                  <div key={i} style={{ marginBottom: 8, fontSize: 13 }}>
                    <div style={{ fontWeight: 500 }}>{i + 1}. {q.question}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{q.purpose}</div>
                  </div>
                ))}
              </Card>
            )}

            {state?.asset_manifest?.length > 0 && (
              <Card title={<><i className="ti ti-cube" /> Asset Manifest</>}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {state.asset_manifest.map((a, i) => (
                    <span key={i} style={{
                      padding: '3px 10px', borderRadius: 16, fontSize: 11,
                      fontFamily: 'var(--mono)',
                      background: a.status === 'matched' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      color: a.status === 'matched' ? '#22c55e' : '#ef4444',
                      border: '1px solid ' + (a.status === 'matched' ? '#22c55e33' : '#ef444433'),
                    }}>{a.entity}: {a.status === 'matched' ? a.asset_id : 'gap'}</span>
                  ))}
                </div>
              </Card>
            )}

            {awaitingApproval && (
              <Card className="" style={{ borderColor: 'var(--accent-amber)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <i className="ti ti-user-check" style={{ fontSize: 22, color: 'var(--accent-amber)' }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600 }}>Human approval required</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                      Lint passed. Review the ontology + rule above, then approve to publish.
                    </div>
                  </div>
                  <button className="btn btn-primary" onClick={approve} disabled={busy}>
                    <i className="ti ti-check" /> Approve & Publish
                  </button>
                </div>
              </Card>
            )}

            {state?.published_bundle && (
              <Card style={{ borderColor: 'var(--accent-green)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <i className="ti ti-circle-check-filled" style={{ fontSize: 22, color: 'var(--accent-green)' }} />
                  <div>
                    <div style={{ fontWeight: 600 }}>Published: {state.published_bundle}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                      Now go to <b>Build a Twin</b> and describe a {state.domain} facility —
                      the Concierge will build it from this bundle. Loop closed.
                    </div>
                  </div>
                </div>
              </Card>
            )}
          </div>

          <Card title="Author Pipeline">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {STAGES.map((s, i) => {
                const done = stage > i
                const active = stage === i
                const color = done ? 'var(--accent-green)' : active ? 'var(--accent-blue)' : 'var(--hint)'
                return (
                  <div key={s.key} style={{ display: 'flex', gap: 9, alignItems: 'center', padding: '7px 4px' }}>
                    <i className={`ti ${done ? 'ti-circle-check-filled' : s.icon}`} style={{ color, fontSize: 16 }} />
                    <span style={{ fontSize: 12, fontWeight: active ? 600 : 400,
                                   color: active ? 'var(--text)' : 'var(--muted)' }}>{s.label}</span>
                    {active && <span className="spinner" style={{ marginLeft: 'auto', width: 11, height: 11 }} />}
                  </div>
                )
              })}
            </div>
            {state?.lint_result && (
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)', fontSize: 11 }}>
                <div>lint: <b style={{ color: lintOk ? 'var(--accent-green)' : 'var(--accent-red)' }}>{lintOk ? 'passed' : 'issues'}</b></div>
                {(state.lint_result.issues || []).map((iss, i) => (
                  <div key={i} style={{ color: 'var(--muted)', marginTop: 3 }}>• {iss.reason}</div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

function deriveStage(state) {
  if (!state) return 0
  if (state.published_bundle) return 8         // publisher done
  if (state.lint_result?.ok) return 7          // at the human gate
  if (state.asset_manifest?.length) return 6   // asset curator done
  if (state.elicitation_questions?.length) return 5  // elicitation done
  if (state.rules?.length) return 4            // rule author done
  if (state.behavior_models?.length) return 3  // behavior modeler done
  if (state.ontology_fragment) return 2        // drafter done
  if (state.next_action === 'draft') return 1  // interview complete
  return 0
}
