"""models.py — what a fix procedure is made of.

A Procedure is the teachable form of a fault: the ordered decisions a competent
operator makes between "the twin says the chiller is sick" and "the chiller is
well". It is NOT a document. Each step is a CHOICE with exactly one right answer
and plausible wrong ones, because that is the difference between reading a
runbook and being able to follow one under pressure — and because a choice is
the only form of step that can be scored.

WHY DISTRACTORS ARE THE HARD PART
---------------------------------
The wrong options carry the teaching. An option nobody would pick teaches
nothing and inflates every score; a wrong option that is genuinely tempting —
the action that seems helpful and makes things worse — is the one that turns a
pass into evidence of competence. So each is written as something a real
operator might actually reach for, and `why` explains the consequence rather
than just marking it wrong.

DETERMINISM
-----------
Procedures are static data and scoring is a pure function of the answers, so the
same run always produces the same score. That property is what lets a clearance
mean something: a number that could vary between two identical runs is not
evidence of anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Option:
    """One choice at a step.

    `why` is shown AFTER the operator answers, on right and wrong alike. Telling
    somebody only that they were wrong teaches them to guess differently; telling
    them what the action would have done teaches them the plant.
    """

    key: str
    label: str
    why: str = ""

    def public(self) -> dict:
        return {"key": self.key, "label": self.label}


@dataclass(frozen=True)
class Step:
    """One decision in a procedure."""

    id: str
    phase: str
    title: str
    instruction: str
    options: tuple[Option, ...]
    correct: str
    hint: str = ""
    #: Shown after a correct answer — the reasoning, not just the confirmation.
    rationale: str = ""

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)

    def public(self, *, reveal: bool = False) -> dict:
        """The step as the operator's page sees it.

        `reveal=False` omits the answer key. That omission is the whole reason
        this method exists: shipping `correct` to the browser and trusting the
        page not to look at it makes the score unfalsifiable, so grading happens
        server-side and the client is never told the answer in advance.
        """
        payload = {
            "id": self.id,
            "phase": self.phase,
            "title": self.title,
            "instruction": self.instruction,
            "options": [o.public() for o in self.options],
            "has_hint": bool(self.hint),
        }
        if reveal:
            payload["correct"] = self.correct
            payload["rationale"] = self.rationale
        return payload


@dataclass(frozen=True)
class Procedure:
    """A complete fix procedure, and the faults it teaches.

    `behavior_globs` is what connects a procedure to the twin: when a Finding
    raises a task, the catalog matches the finding's `behavior_id` against these
    patterns to decide which procedure the operator will be taught. That mapping
    lives in data here rather than in a lookup somewhere else, so adding a
    procedure is one edit.
    """

    id: str
    title: str
    summary: str
    domain: str
    behavior_globs: tuple[str, ...]
    steps: tuple[Step, ...]
    difficulty: str = "medium"
    est_minutes: int = 8
    safety: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    def step_at(self, index: int) -> Step | None:
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def summary_public(self) -> dict:
        """The listing shape — no steps, so a catalog page is one small payload."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "est_minutes": self.est_minutes,
            "total_steps": self.total_steps,
            "safety": self.safety,
            "tags": list(self.tags),
        }

    def public(self) -> dict:
        payload = self.summary_public()
        payload["phases"] = list(dict.fromkeys(s.phase for s in self.steps))
        return payload
