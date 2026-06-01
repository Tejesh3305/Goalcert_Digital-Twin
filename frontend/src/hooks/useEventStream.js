/**
 * useEventStream — subscribe to a twin's live mutation feed via SSE.
 *
 * Connects to /api/v1/bus/stream?tenant=… (backed by the Redis/in-memory event
 * bus). Each committed graph mutation arrives as a BusEvent. We keep a rolling
 * buffer of the most recent `max` events plus a `connected` flag.
 *
 * This is the real-time spine: the Live Ops feed, dashboard activity, and (soon)
 * agents all consume the same stream.
 */
import { useEffect, useRef, useState } from 'react'
import api from '../api/client'

export function useEventStream(tenant, { max = 100 } = {}) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const esRef = useRef(null)

  useEffect(() => {
    if (!tenant) return
    setEvents([])
    setConnected(false)

    const es = new EventSource(api.streamUrl(tenant))
    esRef.current = es

    es.addEventListener('ready', () => setConnected(true))
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data)
        setEvents((prev) => {
          const next = [ev, ...prev]
          return next.length > max ? next.slice(0, max) : next
        })
      } catch { /* ignore keepalives / parse errors */ }
    }
    es.onerror = () => {
      setConnected(false)
      // EventSource auto-reconnects; nothing to do here.
    }

    return () => { es.close(); esRef.current = null }
  }, [tenant, max])

  return { events, connected }
}
