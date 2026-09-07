/**
 * RunPlayer.jsx — the graded procedure, one decision at a time.
 *
 * Shared by both places a procedure is run: against an assigned fault
 * (`FixIssue`) and as training (`Drill`). Those two differ ONLY in how the run
 * starts and what finishing it means — the questions, grading, hints, scoring
 * and debrief are the same, so they live here once rather than being written
 * out twice and drifting.
 *
 * `aside` is the twin panel. It sits in a second column rather than inside the
 * step card because the operator needs to look at the plant WHILE deciding, not
 * after scrolling past the question.
 *
 * THE ANSWER KEY IS NOT IN THIS FILE, AND CANNOT BE.
 *
 * The server sends a question and its options; it does NOT send which option is
 * right (see `Step.public()` in scenario/models.py). Every answer is graded by
 * `POST /scenario/runs/{id}/answer` and this component is told the outcome
 * afterwards. That is what makes the score evidence rather than a claim the
 * browser made about itself — there is no scoring logic here, only rendering of
 * what came back.
 *
 * A wrong answer is not fatal. The operator stays on the step and tries again,
 * because the goal is that they LEARN the procedure; the score records how much
 * help they needed, which is the signal worth having.
 */

import { useCallback, useState } from 'react'
import api from '../../api/client'
import { Card } from '../ui/Card'
import { ErrorBox } from '../ui/States'

export default function RunPlayer({ run, setRun, header, onFinished, footer, aside }) {
  const [picked, setPicked] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [hint, setHint] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = useCallback(async () => {
    if (!picked || !run) return
    setBusy(true)
    try {
      const res = await api.scenario.answer(run.run_id, run.step_index, picked)
      setFeedback({ correct: res.correct, why: res.why, rationale: res.rationale })
      setRun(res.run)
      setHint(null)
      if (res.correct) setPicked(null)
      if (res.finished && onFinished) onFinished(res)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [picked, run, setRun, onFinished])

  const askHint = useCallback(async () => {
    if (!run) return
    setBusy(true)
    try {
      const res = await api.scenario.hint(run.run_id)
      setHint(res)
      setRun((r) => ({ ...r, score: res.score, hints_used: (r.hints_used || 0) + 1 }))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [run, setRun])

  if (!run) return null

  const step = run.step
  const finished = run.finished || run.status === 'completed'
  const passed = run.score >= run.pass_score

  return (
    <div className="panel run-layout">
      <div className="run-main">
      {header}

      {run.procedure?.safety && !finished && (
        <div className="fix-safety">
          <i className="ti ti-alert-triangle" aria-hidden="true" /> {run.procedure.safety}
        </div>
      )}

      <div className="fix-progress">
        {Array.from({ length: run.total_steps }).map((_, i) => (
          <span key={i}
                className={`fix-pip ${i < run.step_index ? 'done'
                  : i === run.step_index && !finished ? 'now' : ''}`} />
        ))}
        <span className="fix-progress-label">
          {finished ? 'Complete' : `Step ${run.step_index + 1} of ${run.total_steps}`}
        </span>
      </div>

      {error && <ErrorBox error={error} />}

      {!finished && step && (
        <Card className="fix-step">
          <div className="fix-phase">{step.phase}</div>
          <h2 className="fix-step-title">{step.title}</h2>
          <p className="fix-instruction">{step.instruction}</p>

          <div className="fix-options">
            {step.options.map((o) => {
              const isPicked = picked === o.key
              const wasWrong = feedback && !feedback.correct && isPicked
              return (
                <button
                  key={o.key}
                  className={`fix-option ${isPicked ? 'picked' : ''} ${wasWrong ? 'wrong' : ''}`}
                  disabled={busy}
                  onClick={() => { setPicked(o.key); setFeedback(null) }}
                >
                  <span className="fix-option-key">{o.key}</span>
                  <span className="fix-option-label">{o.label}</span>
                </button>
              )
            })}
          </div>

          {feedback && (
            <div className={`fix-feedback ${feedback.correct ? 'good' : 'bad'}`}>
              <div className="fix-feedback-head">
                <i className={`ti ${feedback.correct ? 'ti-circle-check' : 'ti-circle-x'}`}
                   aria-hidden="true" />
                {feedback.correct ? 'Correct' : 'Not that one'}
              </div>
              {feedback.why && <p>{feedback.why}</p>}
              {feedback.rationale && <p className="fix-rationale">{feedback.rationale}</p>}
            </div>
          )}

          {hint && (
            <div className="fix-hint">
              <i className="ti ti-bulb" aria-hidden="true" /> {hint.hint}
              <span className="fix-hint-cost">−{hint.cost} points</span>
            </div>
          )}

          <div className="fix-actions">
            <button className="btn btn-primary" disabled={!picked || busy} onClick={submit}>
              {busy ? 'Checking…' : 'Submit answer'}
            </button>
            {step.has_hint && !hint && (
              <button className="btn btn-ghost" disabled={busy} onClick={askHint}>
                <i className="ti ti-bulb" aria-hidden="true" /> Hint (costs points)
              </button>
            )}
          </div>
        </Card>
      )}

      {finished && (
        <Card className="fix-result">
          <div className={`fix-verdict ${passed ? 'pass' : 'fail'}`}>
            <i className={`ti ${passed ? 'ti-shield-check' : 'ti-refresh'}`} aria-hidden="true" />
            <div>
              <div className="fix-verdict-title">
                {passed ? 'Procedure complete' : 'Not passed'}
              </div>
              <div className="fix-verdict-sub">
                Scored {Math.round(run.score)}% · {run.wrong_steps} wrong{' '}
                {run.wrong_steps === 1 ? 'answer' : 'answers'} · {run.hints_used}{' '}
                {run.hints_used === 1 ? 'hint' : 'hints'}
              </div>
            </div>
          </div>

          <div className="fix-transcript">
            {(run.transcript || []).map((entry, i) => (
              <div key={i} className={`fix-tr-row ${entry.correct ? 'ok' : 'no'}`}>
                <i className={`ti ${entry.correct ? 'ti-check' : 'ti-x'}`} aria-hidden="true" />
                <span className="fix-tr-title">{entry.title}</span>
                <span className="fix-tr-choice">{entry.choice_label}</span>
              </div>
            ))}
          </div>

          {footer}
        </Card>
      )}
      </div>

      {/* The twin, running beside the procedure. See TwinPanel for why this is
          present in practice as well as on a real fault. */}
      {aside}
    </div>
  )
}
