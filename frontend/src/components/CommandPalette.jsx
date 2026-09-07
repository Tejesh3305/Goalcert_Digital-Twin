/**
 * CommandPalette — a Cmd/Ctrl+K spotlight over the whole app: navigate pages,
 * open any twin (live or simulated), and toggle dark mode. Ported from the
 * Collins demo and wired to the v3 router + TwinContext.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWork } from '../context/WorkContext'
import { workspaceFor } from '../persona/nav'
import { useTwin } from '../context/TwinContext'
import { domainMeta } from '../lib/machine'
import { SIM_TWINS, simTenantFor } from '../lib/simTwins'

function Palette({ commands, onClose }) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])
  useEffect(() => { setSelected(0) }, [query])

  const filtered = query.trim()
    ? commands.filter((c) => `${c.label} ${c.group || ''} ${c.hint || ''}`.toLowerCase().includes(query.toLowerCase()))
    : commands

  function onKeyDown(e) {
    if (e.key === 'Escape') { onClose(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, filtered.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)) }
    if (e.key === 'Enter' && filtered[selected]) { filtered[selected].action(); onClose() }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'flex-start',
      justifyContent: 'center', paddingTop: '15vh' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(11,13,24,.55)', backdropFilter: 'blur(6px)' }} onClick={onClose} />
      <div style={{ position: 'relative', width: '100%', maxWidth: 520, background: 'var(--surface)',
        border: '1px solid var(--border)', borderRadius: 18, boxShadow: '0 24px 80px rgba(22,19,31,.35)',
        overflow: 'hidden', animation: 'fadeIn .15s ease' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <i className="ti ti-search" />
          <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
            placeholder="Search commands…"
            style={{ flex: 1, border: 'none', outline: 'none', fontSize: 15, background: 'transparent', color: 'var(--text)', fontFamily: 'var(--font)' }} />
          <kbd style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--muted)', fontFamily: 'var(--mono)' }}>ESC</kbd>
        </div>
        <div style={{ maxHeight: 340, overflowY: 'auto', padding: '6px 0' }}>
          {filtered.length === 0 && (
            <div style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>No matching commands</div>
          )}
          {filtered.map((cmd, i) => (
            <div key={cmd.id || i} onClick={() => { cmd.action(); onClose() }} onMouseEnter={() => setSelected(i)}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', cursor: 'pointer',
                background: i === selected ? 'var(--brand-soft)' : 'transparent',
                borderLeft: i === selected ? '3px solid var(--brand)' : '3px solid transparent', transition: 'background .08s' }}>
              <div style={{ width: 30, height: 30, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 15, flexShrink: 0, background: i === selected ? 'var(--brand)' : 'var(--surface2)', color: i === selected ? '#fff' : 'var(--muted)' }}>
                <i className={`ti ${cmd.icon || 'ti-command'}`} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{cmd.label}</div>
                {cmd.hint && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{cmd.hint}</div>}
              </div>
              {cmd.group && (
                <span style={{ fontSize: 9, padding: '2px 7px', borderRadius: 99, background: 'var(--surface2)', color: 'var(--muted)', fontWeight: 600, fontFamily: 'var(--mono)' }}>{cmd.group}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function CommandPalette() {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const { twins, setActiveTenant } = useTwin()

  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((o) => !o) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // The palette offers this persona's destinations only. A supervisor being
  // able to jump to an operator's page would just bounce off PersonaRouter's
  // redirect, which is a worse answer than not offering it.
  const { persona } = useWork()
  const personaNav = workspaceFor(persona).nav

  const commands = useMemo(() => {
    const go = (path) => () => nav(path)
    const openTwin = (tid) => () => { setActiveTenant(tid); nav('/') }
    const cmds = [
      ...personaNav.filter((n) => n.path).map((n) => ({
        id: `nav:${n.id}`, label: n.label, icon: n.icon, group: 'Navigate', action: go(n.path),
      })),
      ...twins.map((t) => ({
        id: `twin:${t.tenant_id}`, label: `Open ${t.name}`, icon: domainMeta(t.domain).icon,
        group: 'Twin', hint: domainMeta(t.domain).label, action: openTwin(t.tenant_id),
      })),
      ...SIM_TWINS.map((s) => ({
        id: `sim:${s.domain}`, label: `Open ${s.label}`, icon: s.icon, group: 'Twin',
        hint: s.tag, action: openTwin(simTenantFor(s.domain)),
      })),
      {
        id: 'theme', label: 'Toggle dark mode', icon: 'ti-moon', group: 'Action',
        action: () => {
          const t = document.documentElement.getAttribute('data-theme') === 'dark' ? '' : 'dark'
          document.documentElement.setAttribute('data-theme', t)
          try { localStorage.setItem('theme', t) } catch { /* */ }
        },
      },
    ]
    return cmds
  }, [nav, twins, setActiveTenant, personaNav])

  if (!open) return null
  return <Palette commands={commands} onClose={() => setOpen(false)} />
}
