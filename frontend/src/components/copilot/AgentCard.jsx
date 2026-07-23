/**
 * AgentCard — one runnable agent: what it does, a Run button, and its output.
 *
 * `slow` marks the agents that run with extended thinking. Those genuinely take
 * 30–70s, so the card says so BEFORE you click and shows a live counter while
 * it works. An unexplained 70-second spinner is indistinguishable from a hang.
 */
import { AiBadge, StubWarning } from './AiBadge'
import { ErrorBox } from '../ui/States'

export default function AgentCard({
  icon, title, description, slow, cta = 'Run',
  agent,                 // from useAgent(): { data, error, loading, elapsed, run }
  onRun,
  disabled, disabledHint,
  children,              // renderer for agent.data
}) {
  const { data, error, loading, elapsed } = agent

  return (
    <div className="agent-card">
      <div className="agent-card-head">
        <div className="agent-card-icon"><i className={`ti ${icon}`} /></div>
        <div className="agent-card-titles">
          <div className="agent-card-title">
            {title}
            {slow && !loading && !data && (
              <span className="pill pill-surface agent-card-slow" title="Runs with extended thinking">
                <i className="ti ti-clock" /> ~{slow}s
              </span>
            )}
          </div>
          <div className="agent-card-desc">{description}</div>
        </div>
        <div className="agent-card-actions">
          {data?.ai && <AiBadge ai={data.ai} elapsed={elapsed} />}
          <button
            className={`btn ${data ? 'btn-ghost' : 'btn-primary'}`}
            disabled={loading || disabled}
            title={disabled ? disabledHint : undefined}
            onClick={onRun}
          >
            {loading
              ? <><span className="spinner" /> {elapsed.toFixed(0)}s</>
              : <><i className={`ti ${data ? 'ti-refresh' : 'ti-player-play'}`} /> {data ? 'Re-run' : cta}</>}
          </button>
        </div>
      </div>

      {loading && (
        <div className="agent-card-progress">
          <div className="agent-card-progress-bar" />
          <span className="hint">
            {slow ? 'Reasoning with extended thinking — this one takes a while.' : 'Working…'}
          </span>
        </div>
      )}

      {error && <ErrorBox error={error} hint="The agent endpoint returned an error." />}

      {data && !loading && (
        <div className="agent-card-body">
          <StubWarning ai={data.ai} />
          {children ? children(data) : null}
        </div>
      )}
    </div>
  )
}
