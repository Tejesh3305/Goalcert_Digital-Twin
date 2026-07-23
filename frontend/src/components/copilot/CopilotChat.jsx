/**
 * CopilotChat — the conversational agents.
 *
 * Two modes, same component because the interaction is identical:
 *   'dashboard'    live-state Q&A over the twin's current telemetry
 *   'troubleshoot' the AI Mechanic — multi-turn diagnostic questioning that
 *                  narrows a hypothesis and reports its confidence
 *
 * Both are grounded server-side in the live twin, so no telemetry needs to be
 * shipped up from here — just the tenant.
 */
import { useEffect, useRef, useState } from 'react'
import { AiBadge } from './AiBadge'
import Markdown from './Markdown'
import api from '../../api/client'

const QUICK = {
  dashboard: [
    'What is the current status?',
    'Is anything trending badly?',
    'Which sensor is closest to its limit?',
    'What should I watch over the next hour?',
  ],
  troubleshoot: [
    'Where should I start?',
    'The temperature looks high to me',
    'It started after the last duty cycle',
    'What would you inspect first?',
  ],
}

export default function CopilotChat({ tenant, machine, domain, mode = 'dashboard', height = 320 }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [hypothesis, setHypothesis] = useState(null)
  const endRef = useRef(null)

  useEffect(() => { setMessages([]); setHypothesis(null) }, [tenant, mode])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const send = async (q) => {
    const text = (q ?? input).trim()
    if (!text || busy) return
    setInput('')
    const history = messages.map((m) => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.text }))
    setMessages((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const body = { tenant, machine, domain, message: text, messages: history, history }
      const res = mode === 'troubleshoot'
        ? await api.copilot.troubleshoot(body)
        : await api.copilot.dashboardChat(body)
      const reply = mode === 'troubleshoot' ? res.reply : res.reply
      setMessages((m) => [...m, { role: 'ai', text: reply, ai: res.ai }])
      if (mode === 'troubleshoot') {
        setHypothesis(res.hypothesis
          ? { text: res.hypothesis, confidence: res.confidence, resolved: res.resolved }
          : null)
      }
    } catch (e) {
      setMessages((m) => [...m, { role: 'ai', text: `Couldn't reach the agent: ${e.message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="copilot-chat">
      {hypothesis && (
        <div className={`hypothesis ${hypothesis.resolved ? 'is-resolved' : ''}`}>
          <div className="hypothesis-label">
            <i className="ti ti-target" /> Leading hypothesis
            {hypothesis.resolved && <span className="pill pill-green" style={{ fontSize: 10 }}>conclusive</span>}
          </div>
          <div className="hypothesis-text">{hypothesis.text}</div>
          <div className="hypothesis-bar">
            <div className="hypothesis-bar-fill" style={{ width: `${(hypothesis.confidence || 0) * 100}%` }} />
          </div>
          <div className="hint">{Math.round((hypothesis.confidence || 0) * 100)}% confidence</div>
        </div>
      )}

      <div className="chat-quick">
        {QUICK[mode].map((q) => (
          <div key={q} className="quick-chip" onClick={() => send(q)}>{q}</div>
        ))}
      </div>

      <div className="chat-wrap">
        <div className="chat-messages" style={{ height, maxHeight: height }}>
          {messages.length === 0 && !busy && (
            <div className="empty" style={{ padding: '18px 0' }}>
              <i className="ti ti-message-chatbot" style={{ fontSize: 22, display: 'block', marginBottom: 6 }} />
              {mode === 'troubleshoot'
                ? 'Describe the symptom — the mechanic will ask diagnostic questions back.'
                : 'Ask anything about this twin’s current state.'}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-ai'}`}>
              {m.role === 'ai' && (
                <div className="bubble-label">
                  {mode === 'troubleshoot' ? 'AI Mechanic' : 'Twin Copilot'}
                  {m.ai && <AiBadge ai={m.ai} />}
                </div>
              )}
              {m.role === 'ai' && !m.error ? <Markdown>{m.text}</Markdown> : m.text}
            </div>
          ))}
          {busy && (
            <div className="chat-bubble bubble-ai">
              <div className="bubble-label">{mode === 'troubleshoot' ? 'AI Mechanic' : 'Twin Copilot'}</div>
              <span className="spinner" /> <span className="hint">thinking…</span>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="chat-input-row">
          <input
            className="input"
            value={input}
            placeholder={busy ? 'Waiting for the agent…' : 'Ask about this twin…'}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
          />
          <button className="btn btn-primary" disabled={busy || !input.trim()} onClick={() => send()}>
            <i className="ti ti-send" />
          </button>
        </div>
      </div>
    </div>
  )
}
