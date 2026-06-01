/**
 * ToastContext — tiny notification system. `toast.ok/err/info(title, msg)`
 * from anywhere; toasts auto-dismiss. Used for create/delete/validation
 * feedback (e.g. the SHACL gate rejecting a malformed asset).
 */
import { createContext, useCallback, useContext, useState } from 'react'

const ToastContext = createContext(null)
let _id = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const push = useCallback((kind, title, msg, ttl = 4200) => {
    const id = ++_id
    setToasts((t) => [...t, { id, kind, title, msg }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl)
  }, [])

  const toast = {
    ok: (title, msg) => push('ok', title, msg),
    err: (title, msg) => push('err', title, msg),
    info: (title, msg) => push('', title, msg),
  }

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toast-wrap">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <div className="toast-title">{t.title}</div>
            {t.msg && <div className="toast-msg">{t.msg}</div>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
