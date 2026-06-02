import { useEffect, useRef, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { useToast } from '../context/ToastContext'
import { useTwin } from '../context/TwinContext'
import api from '../api/client'

/**
 * Build a Twin — the Concierge agent flow, end to end.
 *
 * A chat with the Concierge. Behind it runs the full twin-building graph
 * (Concierge → Classifier → Composer → Validator → Graph Writer). As the agents
 * progress, the pipeline rail on the right lights up, and on commit a real twin
 * lands in the graph (and in the Twins switcher).
 */
const STAGES = [
  { key: 'concierge', label: 'Concierge', icon: 'ti-message-2', hint: 'Understands your facility' },
  { key: 'classifier', label: 'Classifier', icon: 'ti-category', hint: 'Picks the domain' },
  { key: 'composer', label: 'Composer', icon: 'ti-puzzle', hint: 'Loads a capability bundle' },
  { key: 'validator', label: 'Validator', icon: 'ti-shield-check', hint: 'SHACL-validates the draft' },
  { key: 'graph_writer', label: 'Graph Writer', icon: 'ti-database', hint: 'Commits the live twin' },
]

export default function Concierge() {
  const toast = useToast()
  const { refreshTwins, setActiveTenant } = useTwin()
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
      const res = await api.twinAgentStart({})
      setSession(res.session_id)
      setState(res.state)
      setMessages([{ role: 'ai', text: res.state.reply_to_user || 'Hi! What facility shall we model?' }])
    } catch (e) {
      toast.err('Could not start the agent', e.message)
    } finally { setBusy(false) }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || !session) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.twinAgentMessage(session, text)
      const s = res.state
      setState(s)
      if (s.reply_to_user) setMessages((m) => [...m, { role: 'ai', text: s.reply_to_user }])
      if (s.committed && s.twin_id) {
        toast.ok('Twin committed', `${s.twin_name || s.twin_id} is live`)
        await refreshTwins()
        setActiveTenant(s.twin_id)
      }
    } catch (e) {
      toast.err('Agent error', e.message)
    } finally { setBusy(false) }
  }

  // Which stage are we at? Derive from state for the rail.
  const stageIndex = deriveStage(state)

  return (
    <div className="panel">
      <PanelHeader
        title="Build a Twin"
        subtitle="Chat with the Concierge — five agents turn it into a live, validated twin."
      >
        {!session
          ? <button className="btn btn-primary" onClick={start} disabled={busy}>
              <i className="ti ti-sparkles" /> Start
            </button>
          : <button className="btn" onClick={() => { setSession(null); setState(null); setMessages([]) }}>
              <i className="ti ti-refresh" /> New session
            </button>}
      </PanelHeader>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 260px', gap: 12 }}>
        <Card>
          {!session ? (
            <div className="empty" style={{ border: 'none' }}>
              <i className="ti ti-sparkles" style={{ fontSize: 28, display: 'block', marginBottom: 8, color: 'var(--accent-blue)' }} />
              Click <b>Start</b>, then describe your facility in plain language —
              e.g. “a server room cooled by an air handler, monitor temperature.”
            </div>
          ) : (
            <div className="chat-wrap" style={{ height: 460 }}>
              <div className="chat-messages">
                {messages.map((m, i) => (
                  <div key={i} className={`chat-bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-ai'}`}>
                    {m.role === 'ai' && <div className="bubble-label">Concierge</div>}
                    {m.text}
                  </div>
                ))}
                {busy && <div className="chat-bubble bubble-ai"><span className="spinner" /> thinking…</div>}
                <div ref={endRef} />
              </div>
              <div className="chat-input-row">
                <input className="input" value={input} placeholder="Describe your facility…"
                       disabled={busy}
                       onChange={(e) => setInput(e.target.value)}
                       onKeyDown={(e) => e.key === 'Enter' && send()} />
                <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
                  <i className="ti ti-send" />
                </button>
              </div>
            </div>
          )}
        </Card>

        <Card title="Agent Pipeline">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {STAGES.map((s, i) => {
              const done = stageIndex > i || (state?.committed && i <= 4)
              const active = stageIndex === i && !state?.committed
              const color = done ? 'var(--accent-green)' : active ? 'var(--accent-blue)' : 'var(--hint)'
              return (
                <div key={s.key} style={{ display: 'flex', gap: 9, alignItems: 'flex-start', padding: '7px 4px' }}>
                  <i className={`ti ${done ? 'ti-circle-check-filled' : s.icon}`}
                     style={{ color, fontSize: 16, marginTop: 1 }} />
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: active ? 'var(--text)' : 'var(--muted)' }}>
                      {s.label}{active && <span className="spinner" style={{ marginLeft: 6, width: 10, height: 10 }} />}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--hint)' }}>{s.hint}</div>
                  </div>
                </div>
              )
            })}
          </div>
          {state && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--muted)' }}>
              {state.domain && <div>domain: <b style={{ color: 'var(--text)' }}>{state.domain}</b>
                {state.domain_confidence != null && ` (${Math.round(state.domain_confidence * 100)}%)`}</div>}
              {state.loaded_bundles?.length > 0 && <div>bundle: <b style={{ color: 'var(--accent-blue)' }}>{state.loaded_bundles.join(', ')}</b></div>}
              {state.validation && <div>validation: <b style={{ color: state.validation.ok ? 'var(--accent-green)' : 'var(--accent-red)' }}>{state.validation.ok ? 'passed' : 'failed'}</b></div>}
              {state.committed && <div style={{ color: 'var(--accent-green)', fontWeight: 600, marginTop: 4 }}><i className="ti ti-check" /> Twin committed</div>}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function deriveStage(state) {
  if (!state) return 0
  if (state.committed) return 5
  if (state.validation) return state.validation.ok ? 4 : 0
  if (state.loaded_bundles?.length) return 3
  if (state.domain) return 2
  if (state.next_action === 'classify') return 1
  return 0
}
