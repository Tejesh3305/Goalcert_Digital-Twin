import { useState, useRef, useEffect } from 'react'
import { PanelHeader, Card } from '../components/ui/Card'
import MockBanner from '../components/ui/MockBanner'
import NoTwin from '../components/NoTwin'
import { useTwin } from '../context/TwinContext'
import { usePolling } from '../hooks/useApi'
import api from '../api/client'

/**
 * Operational Copilot — MOCK (with a touch of real grounding).
 *  The chat is canned/rule-based. It DOES read the live stats so a couple of
 *  answers reflect the real twin. A real copilot needs an LLM wired to the
 *  Graph Query API + schema service as tools (see PLACEHOLDERS.md → "Copilot").
 */
const QUICK = [
  'How many findings are open?',
  'What assets does this twin have?',
  'Explain the change log',
  'How do I add a sensor?',
]

export default function Copilot() {
  const { activeTenant, activeTwin } = useTwin()
  const { data: stats } = usePolling(
    () => api.stats(activeTenant), 4000, [activeTenant], { skip: !activeTenant },
  )
  const [messages, setMessages] = useState([{
    role: 'ai',
    text: 'I can answer questions about this twin. The platform exposes a schema-query API and the graph read API — a full LLM copilot would use those as tools. For now I answer from live stats and a small playbook.',
  }])
  const [input, setInput] = useState('')
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  if (!activeTenant) return <NoTwin />

  const answer = (q) => {
    const l = q.toLowerCase()
    if (l.includes('finding')) return `This twin has ${stats?.total_findings ?? 0} findings recorded (${stats?.finding_severity?.critical || 0} critical).`
    if (l.includes('asset') || l.includes('have')) {
      const c = stats?.entity_counts || {}
      return `Entities: ${Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') || 'none yet'}. Total ${stats?.total_entities || 0}.`
    }
    if (l.includes('change log') || l.includes('changelog')) return 'The change log is an append-only, SHA-256 hash-chained ledger. Every mutation that passes the Graph Writer is recorded; altering any past event breaks the chain from that point on.'
    if (l.includes('sensor') || l.includes('add')) return 'Go to Asset Graph → Add Asset, pick a type (e.g. hvac:TemperatureSensor), name it, and optionally relate it to an existing entity. It is validated by the SHACL gate before it is written.'
    return `I don't have a grounded answer for that yet. A full copilot would query the graph and ontology to respond. Current twin: ${activeTwin?.name}.`
  }

  const send = (q) => {
    const text = (q ?? input).trim()
    if (!text) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text }])
    setTimeout(() => setMessages((m) => [...m, { role: 'ai', text: answer(text) }]), 400)
  }

  return (
    <div className="panel">
      <PanelHeader title="Operational Copilot" subtitle="Natural-language access to your twin" />
      <MockBanner what="Replies are rule-based with live-stat grounding; a real copilot wires an LLM to the graph + schema APIs as tools." />
      <Card>
        <div className="chat-quick">
          {QUICK.map((q) => <div key={q} className="quick-chip" onClick={() => send(q)}>{q}</div>)}
        </div>
        <div className="chat-wrap">
          <div className="chat-messages">
            {messages.map((m, i) => (
              <div key={i} className={`chat-bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-ai'}`}>
                {m.role === 'ai' && <div className="bubble-label">NextXR Copilot</div>}
                {m.text}
              </div>
            ))}
            <div ref={endRef} />
          </div>
          <div className="chat-input-row">
            <input className="input" value={input} placeholder="Ask about this twin…"
                   onChange={(e) => setInput(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && send()} />
            <button className="btn btn-primary" onClick={() => send()}><i className="ti ti-send" /></button>
          </div>
        </div>
      </Card>
    </div>
  )
}
