/**
 * useApi — run an async API call with {data, loading, error, refetch}.
 * usePolling — same, but re-runs on an interval (for near-live panels that
 *   don't have a dedicated SSE stream).
 *
 * `deps` controls when the call re-fires (like useEffect deps). Pass the
 * fetcher as a stable function or inline; we re-run whenever deps change.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export function useApi(fetcher, deps = [], { skip = false } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(!skip)
  const [error, setError] = useState(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const run = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetcherRef.current()
      setData(result)
      return result
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (skip) { setLoading(false); return }
    run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, loading, error, refetch: run }
}

export function usePolling(fetcher, intervalMs = 3000, deps = [], { skip = false } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const run = useCallback(async () => {
    try {
      setData(await fetcherRef.current())
      setError(null)
    } catch (e) {
      setError(e)
    }
  }, [])

  useEffect(() => {
    if (skip) return
    run()
    const id = setInterval(run, intervalMs)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, skip])

  return { data, error, refetch: run }
}
