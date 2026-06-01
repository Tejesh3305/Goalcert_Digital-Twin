/** A banner shown atop panels that are presentational placeholders — UI is
 *  real, data is illustrative, and the backing feature isn't in the core yet.
 *  `what` is a one-line description of what real-ing it would take. */
export default function MockBanner({ what }) {
  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      background: 'rgba(224,150,47,.08)', border: '1px solid rgba(224,150,47,.25)',
      borderRadius: 8, padding: '9px 12px', marginBottom: 16, fontSize: 11.5,
    }}>
      <i className="ti ti-flask" style={{ color: 'var(--accent-amber)', fontSize: 15, marginTop: 1 }} />
      <div>
        <b style={{ color: 'var(--accent-amber)' }}>Demo placeholder.</b>{' '}
        <span className="muted">{what} See PLACEHOLDERS.md for what building it for real involves.</span>
      </div>
    </div>
  )
}
