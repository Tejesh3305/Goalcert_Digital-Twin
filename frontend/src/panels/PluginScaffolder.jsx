import { useEffect, useRef, useState } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import { useToast } from '../context/ToastContext'
import api from '../api/client'

/**
 * Plugin Scaffolder — Team 4: Platform Extension.
 *
 * Chat with the Plugin Interviewer to define an extension, then the Scaffolder
 * generates Plugin SDK boilerplate for one of 6 extension points:
 *   adapter | behavior | view | webhook | transform | auth
 */
const STAGES = [
  { key: 'interview', label: 'Interviewer', icon: 'ti-microphone' },
  { key: 'scaffold', label: 'Scaffolder', icon: 'ti-code' },
]

export default function PluginScaffolder() {
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
      const res = await api.pluginStart()
      setSession(res.session_id)
      setState(res.state)
      setMessages([{ role: 'ai', text: res.state.reply_to_user || 'What kind of plugin do you want to build?' }])
    } catch (e) { toast.err('Could not start', e.message) } finally { setBusy(false) }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || !session) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.pluginMessage(session, text)
      setState(res.state)
      if (res.state.reply_to_user) setMessages((m) => [...m, { role: 'ai', text: res.state.reply_to_user }])
    } catch (e) { toast.err('Agent error', e.message) } finally { setBusy(false) }
  }

  const deriveStage = () => {
    if (!state) return -1
    if (state.scaffold) return 1
    return 0
  }
  const stage = deriveStage()

  // Init screen
  if (!session) return (
    <div className="panel">
      <PanelHeader title="Plugin SDK" subtitle="Scaffold a platform extension plugin" />
      <Card>
        <p style={{ color: 'var(--text-dim)', marginBottom: 14, fontSize: 14 }}>
          Generate Plugin SDK boilerplate for any of 6 extension points: adapter, behavior, view,
          webhook, transform, or auth. The interviewer will ask what you need, then the scaffolder
          generates the code.
        </p>
        <button className="btn btn-primary" onClick={start} disabled={busy}>
          {busy ? 'Starting...' : 'Start Plugin Session'}
        </button>
      </Card>
    </div>
  )

  return (
    <div className="panel">
      <PanelHeader title="Plugin SDK" subtitle="Scaffold a platform extension plugin" />

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
              placeholder="Describe your plugin..." disabled={busy} />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
              Send
            </button>
          </div>
        )}
      </Card>

      {/* Scaffold output */}
      {state?.scaffold && (
        <Card style={{ marginTop: 14, borderColor: 'var(--accent)' }}>
          <h4 style={{ margin: '0 0 10px', color: 'var(--accent)' }}>
            <i className="ti-code" style={{ marginRight: 6 }} />Generated Scaffold
          </h4>
          {state.scaffold.files?.map((f, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text-dim)', marginBottom: 4 }}>
                {f.path} ({f.language})
              </div>
              <pre style={{
                background: '#1e1e1e', color: '#d4d4d4', padding: 14, borderRadius: 8,
                fontSize: 12, overflowX: 'auto', maxHeight: 300,
              }}>{f.content}</pre>
            </div>
          ))}
          {state.scaffold.readme && (
            <div style={{ fontSize: 13, color: 'var(--text-dim)', marginTop: 8 }}>
              {state.scaffold.readme}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
