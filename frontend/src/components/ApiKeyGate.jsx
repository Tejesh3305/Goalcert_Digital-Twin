import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

/**
 * ApiKeyGate — unlocks the STANDALONE app when the server runs with NXR_API_KEYS set.
 *
 * The backend is dev-permissive only while NXR_API_KEYS is UNSET; a deployment that
 * sets it (as prod should — see TWIN_INTEGRATION_PLAN.md) rejects every /api call the
 * browser makes, because a fresh visitor has no key in localStorage. The app shell then
 * renders but every panel shows "Missing X-API-Key header", which reads as "the backend
 * is down" when the backend is in fact healthy and correctly refusing an anonymous call.
 *
 * This gate probes one authenticated endpoint and, ONLY on a 401, asks for a key and
 * stores it under `nxr_api_key` — the exact key api/client.js already sends as X-API-Key.
 *
 * Deliberately mounted from main.jsx (the standalone entry) and nowhere else: under
 * federation the hub enters through mount.jsx and its gateway injects the key
 * server-side, so the hub must never see this prompt.
 */

// Anything other than 401 (network failure, 500, Neo4j down) is NOT an auth problem.
// Blocking the whole app behind this gate for those would hide the real error, so we
// let the app render and each panel surfaces its own state.
const CHECKING = 'checking'
const OPEN = 'open'         // no key needed, or the stored one works
const LOCKED = 'locked'     // server returned 401 — a key is required

export default function ApiKeyGate({ children }) {
  const [state, setState] = useState(CHECKING)
  const [input, setInput] = useState('')
  const [rejected, setRejected] = useState(false)
  const [busy, setBusy] = useState(false)

  const probe = useCallback(async () => {
    try {
      await api.listTwins()
      return true
    } catch (err) {
      if (err?.status === 401) return false
      // Non-auth failure: don't hold the app hostage.
      return true
    }
  }, [])

  useEffect(() => {
    let alive = true
    probe().then((ok) => {
      if (!alive) return
      // A stored key that no longer resolves should be re-prompted, not silently kept.
      if (!ok && localStorage.getItem('nxr_api_key')) setRejected(true)
      setState(ok ? OPEN : LOCKED)
    })
    return () => { alive = false }
  }, [probe])

  async function submit(e) {
    e.preventDefault()
    const key = input.trim()
    if (!key || busy) return
    setBusy(true)
    localStorage.setItem('nxr_api_key', key)
    const ok = await probe()
    if (ok) {
      setState(OPEN)
    } else {
      // Keep the bad key out of storage so a reload re-prompts cleanly.
      localStorage.removeItem('nxr_api_key')
      setRejected(true)
      setInput('')
    }
    setBusy(false)
  }

  if (state === CHECKING) return null
  if (state === OPEN) return children

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)', padding: 24,
    }}>
      <form
        onSubmit={submit}
        className="card"
        style={{ width: '100%', maxWidth: 420, padding: 28 }}
      >
        <div className="card-title" style={{ fontSize: 17 }}>
          <i className="ti ti-lock" /> This twin requires an API key
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 12.5, lineHeight: 1.6, marginTop: 0 }}>
          The server is running with <code>NXR_API_KEYS</code> set, so the API only answers
          authenticated callers. Paste a key to continue — it is stored in this browser only.
        </p>

        <div className="field" style={{ marginTop: 18 }}>
          <label htmlFor="nxr-key">API key</label>
          <input
            id="nxr-key"
            className="input"
            type="password"
            autoFocus
            autoComplete="current-password"
            placeholder="X-API-Key value"
            value={input}
            onChange={(e) => { setInput(e.target.value); setRejected(false) }}
          />
        </div>

        {rejected && (
          <div className="error-box" style={{ marginBottom: 12 }}>
            <i className="ti ti-alert-triangle" /> That key was rejected by the server.
          </div>
        )}

        <button
          type="submit"
          className="btn btn-primary"
          style={{ width: '100%' }}
          disabled={!input.trim() || busy}
        >
          {busy ? 'Checking…' : 'Unlock'}
        </button>
      </form>
    </div>
  )
}
