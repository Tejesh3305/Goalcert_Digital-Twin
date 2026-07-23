/**
 * useAgent — run one copilot agent and track its whole lifecycle.
 *
 * The heavy agents take real time (a work order ~35s, a repair procedure ~70s,
 * because they run with extended thinking). A bare spinner for that long reads
 * as a hang, so this also exposes `elapsed`, letting the UI show a live counter
 * and set expectations up front.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export function useAgent(fn) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const timer = useRef(null)
  const alive = useRef(true)
  const fnRef = useRef(fn)
  fnRef.current = fn

  // Set alive TRUE in the effect body, not just via useRef's one-time init.
  // Under React StrictMode the mount→unmount→remount cycle fires the cleanup
  // (alive=false) but a ref persists across the remount — so without resetting
  // it here, every post-remount setData/setLoading is silently skipped, leaving
  // the agent stuck on its spinner forever even though the request succeeded.
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (timer.current) clearInterval(timer.current)
    }
  }, [])

  const run = useCallback(async (...args) => {
    setLoading(true)
    setError(null)
    setElapsed(0)
    const t0 = Date.now()
    timer.current = setInterval(() => {
      if (alive.current) setElapsed((Date.now() - t0) / 1000)
    }, 100)
    try {
      const res = await fnRef.current(...args)
      if (alive.current) setData(res)
      return res
    } catch (e) {
      if (alive.current) setError(e)
      return null
    } finally {
      clearInterval(timer.current)
      timer.current = null
      if (alive.current) {
        setElapsed((Date.now() - t0) / 1000)
        setLoading(false)
      }
    }
  }, [])

  const reset = useCallback(() => { setData(null); setError(null); setElapsed(0) }, [])

  return { data, error, loading, elapsed, run, reset }
}

export default useAgent
