"""catalog.py — the fix procedures, and which fault each one teaches.

Every procedure here maps to behaviours the twin actually raises (see
`behaviors/cfp/` and `behaviors/hvac/`). That is the constraint the content is
written against: a procedure for a fault this twin cannot detect would never
reach an operator, and a detected fault with no procedure leaves them a task
they cannot learn from.

    behaviors/cfp/tier_c_power.py     ->  ups-on-battery, transformer-over-temp
    behaviors/cfp/tier_c_water.py     ->  water-leak, tank-low-level
    behaviors/cfp/tier_b_chiller.py   ->  chiller-cop-drop
    behaviors/cfp/tier_b_vibration.py ->  pump-vibration
    behaviors/cfp/tier_c_filter.py    ->  filter-clogged
    behaviors/cfp/tier_c_fire.py      ->  smoke-alarm
    behaviors/cfp/tier_c_network.py   ->  heartbeat-loss
    behaviors/cfp/tier_c_security.py  ->  door-forced
    behaviors/hvac/*                  ->  hvac-zone-temp

MATCHING IS MOST-SPECIFIC-FIRST
-------------------------------
`scenario_id_for_behavior` walks procedures in declaration order and takes the
first glob that matches, so `cfp.leak_detected` finds the leak procedure before
any broader `cfp.*` fallback further down. Order is therefore meaningful —
append new specific procedures ABOVE general ones.

ON THE CONTENT ITSELF
---------------------
The distractors are the teaching (see `models.py`). Each wrong option is
something an operator might plausibly reach for under time pressure, and its
`why` explains the consequence. "Call your supervisor" is deliberately NOT the
right answer anywhere except where escalation genuinely is the correct first
action — making it always-correct would teach operators to escalate instead of
diagnose, which is the opposite of the point.
"""
from __future__ import annotations

import fnmatch

from scenario.models import Option, Procedure, Step


def _o(key: str, label: str, why: str = "") -> Option:
    return Option(key=key, label=label, why=why)


# ── Power ───────────────────────────────────────────────────────────────
UPS_ON_BATTERY = Procedure(
    id="ups-on-battery",
    title="UPS transferred to battery",
    summary=("The UPS has dropped its mains feed and is running the critical load "
             "from its battery. You have minutes, not hours."),
    domain="power",
    behavior_globs=("cfp.ups_on_battery", "cfp.generator_fuel_low"),
    difficulty="hard",
    est_minutes=10,
    safety="Do not open the UPS cabinet. Battery strings stay live with mains removed.",
    tags=("power", "critical", "ups"),
    steps=(
        Step(
            id="assess-runtime",
            phase="Assess",
            title="Establish your time budget",
            instruction=("The UPS is on battery. Before touching anything, "
                         "what do you establish first?"),
            options=(
                _o("runtime", "Remaining battery runtime and the load it is carrying",
                   "Correct. Runtime is your entire decision budget — every later "
                   "choice depends on whether you have four minutes or forty."),
                _o("reset", "Reset the UPS to force it back to mains",
                   "A reset while mains is absent drops the critical load instantly. "
                   "This is the action that turns an event into an outage."),
                _o("logs", "Download the UPS event log for the report",
                   "Evidence matters, but not before the load is safe. The log will "
                   "still be there in ten minutes; the battery will not."),
                _o("escalate", "Escalate to the supervisor and wait for instructions",
                   "Escalation is right later. Waiting without knowing the runtime "
                   "means neither of you knows how long there is to act."),
            ),
            correct="runtime",
            hint="Every other decision here is a function of one number.",
            rationale=("Runtime plus load tells you whether this is a controlled "
                       "transfer or an imminent drop, and nothing else you could do "
                       "first changes that."),
        ),
        Step(
            id="find-cause",
            phase="Diagnose",
            title="Locate the loss",
            instruction="Runtime is 18 minutes. Where do you look for the cause?",
            options=(
                _o("upstream", "Upstream: incoming feed, main breaker, ATS position",
                   "Correct. A UPS on battery is reporting a fault it did not cause. "
                   "The cause is almost always upstream of it."),
                _o("battery", "The battery strings, for a failed cell",
                   "A failed cell shortens runtime; it does not cause a transfer. "
                   "You would be diagnosing the symptom's symptom."),
                _o("load", "The load side, for a downstream short",
                   "A downstream fault trips a branch breaker. It does not remove "
                   "the UPS's mains input."),
                _o("firmware", "UPS firmware version and known bugs",
                   "Possible but rare, and not a 18-minute investigation. Exhaust "
                   "the physical causes first."),
            ),
            correct="upstream",
            hint="The UPS is the messenger, not the message.",
            rationale=("A transfer to battery means the UPS lost acceptable input "
                       "power. That condition originates upstream — utility, "
                       "breaker, or transfer switch."),
        ),
        Step(
            id="generator",
            phase="Act",
            title="Secure a longer runway",
            instruction=("The incoming utility feed is dead and the generator has "
                         "not started. What do you do?"),
            options=(
                _o("manual-start", "Start the generator manually and confirm it takes load",
                   "Correct. Auto-start failed, so the standby plant needs a human. "
                   "Confirming it takes load is the half people forget."),
                _o("shed", "Start shedding non-critical load to stretch the battery",
                   "This buys minutes. Starting the generator buys hours — do the "
                   "bigger thing first, then shed if it fails."),
                _o("wait", "Wait — auto-start has a delay and may still fire",
                   "It may. With a dead utility feed and a finite battery, waiting "
                   "spends the one resource you cannot get back."),
                _o("utility", "Call the utility to report the outage",
                   "Necessary, but it does not keep your load up. Delegate it; do "
                   "not do it yourself while on battery."),
            ),
            correct="manual-start",
            hint="Which action changes your time budget by hours rather than minutes?",
            rationale=("A manual start converts a battery countdown into a fuel "
                       "countdown. Verifying it actually takes load matters — a "
                       "generator running unloaded protects nothing."),
        ),
        Step(
            id="verify",
            phase="Verify",
            title="Confirm the twin agrees",
            instruction=("The generator is running and carrying load. How do you "
                         "close this out?"),
            options=(
                _o("confirm-twin", "Confirm the UPS reads on-line and the finding clears in the twin",
                   "Correct. The job is not done because the plant looks right — it "
                   "is done when the system of record agrees."),
                _o("close", "Mark the task fixed; the load is up",
                   "The load being up is necessary, not sufficient. A finding left "
                   "open hides the next real one."),
                _o("recharge", "Wait for the batteries to reach full charge",
                   "Recharge takes hours and does not need you present. Do not hold "
                   "an open incident for it."),
                _o("email", "Write the incident report",
                   "Later. Verification first, or you will be writing a report "
                   "about a state you did not confirm."),
            ),
            correct="confirm-twin",
            hint="What has to be true for the next shift to trust the dashboard?",
            rationale=("A cleared finding is what tells everyone else the fault is "
                       "over. Leaving it open trains people to ignore findings."),
        ),
    ),
)

TRANSFORMER_OVER_TEMP = Procedure(
    id="transformer-over-temp",
    title="Transformer over temperature",
    summary="A distribution transformer is running hotter than its rating allows.",
    domain="power",
    behavior_globs=("cfp.transformer_over_temp",),
    difficulty="hard",
    est_minutes=8,
    safety="High voltage. Thermal readings only — no physical inspection while energised.",
    tags=("power", "thermal"),
    steps=(
        Step(
            id="load-check",
            phase="Assess",
            title="Load or cooling?",
            instruction=("The transformer is above its temperature alarm point. "
                         "What distinguishes the two possible causes?"),
            options=(
                _o("compare", "Compare present load against its rating and recent history",
                   "Correct. An over-temp is either too much heat in or too little "
                   "heat out, and the load trend separates them in one look."),
                _o("fans", "Check whether the cooling fans are running",
                   "Part of the answer, and the second half of it. Establish "
                   "whether the heat input is abnormal first."),
                _o("trip", "Trip the transformer to protect it",
                   "This drops everything downstream. Reserve it for a temperature "
                   "that is still climbing after you have acted."),
                _o("ambient", "Check the ambient temperature in the substation",
                   "Contributory but rarely decisive on its own — a transformer "
                   "within rating tolerates a hot day."),
            ),
            correct="compare",
            hint="Heat in, or heat out — which measurement tells you which?",
            rationale=("Load at or below rating with rising temperature points at "
                       "cooling. Load above rating points at the load."),
        ),
        Step(
            id="cooling",
            phase="Diagnose",
            title="Cooling path",
            instruction="Load is at 78% of rating — normal. Where do you look next?",
            options=(
                _o("cooling-path", "Cooling: fan operation, radiator fouling, oil level",
                   "Correct. Normal load with abnormal temperature means the heat is "
                   "not leaving. Those three cover the whole path."),
                _o("winding", "Suspect an internal winding fault",
                   "Possible, and it would usually bring other signatures with it. "
                   "Rule out the cheap external causes first."),
                _o("sensor", "Assume the temperature sensor has failed",
                   "Tempting, and occasionally right. Assuming it first is how a "
                   "real over-temp gets ignored for a shift."),
                _o("harmonics", "Investigate harmonic distortion from downstream drives",
                   "A real cause of extra heating, but a slow investigation. Not "
                   "the first move on a live alarm."),
            ),
            correct="cooling-path",
            hint="Normal heat in, high temperature. So what must be wrong?",
            rationale=("With load normal, the fault is in heat rejection: fans, "
                       "radiator surface, or coolant level."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Reduce the rise",
            instruction=("Two of four cooling fans have failed. Temperature is stable "
                         "but above alarm. What now?"),
            options=(
                _o("reduce-and-raise", "Reduce load where possible and raise a fan replacement job",
                   "Correct. Cut the heat you can control now, and fix the cause "
                   "properly rather than leaving it derated indefinitely."),
                _o("nothing", "Nothing — it is stable and within short-term limits",
                   "Stable above alarm is not safe. It has no margin left for a hot "
                   "day or a load step."),
                _o("trip-now", "Trip the transformer",
                   "Disproportionate. Temperature is stable and you have a cheaper "
                   "action available."),
                _o("portable", "Point portable fans at the radiator",
                   "A real field expedient, but it is a patch. It should follow the "
                   "load reduction, not replace the repair job."),
            ),
            correct="reduce-and-raise",
            hint="Do something about now, and something about next week.",
            rationale=("Derating restores margin immediately; the replacement job is "
                       "what stops this recurring."),
        ),
    ),
)

# ── Water ───────────────────────────────────────────────────────────────
WATER_LEAK = Procedure(
    id="water-leak",
    title="Water leak detected",
    summary=("Leak detection has tripped, or flow is continuing when the system "
             "should be static."),
    domain="water",
    behavior_globs=("cfp.leak_detected", "cfp.continuous_flow_leak"),
    difficulty="medium",
    est_minutes=7,
    safety="Water near electrical plant. Confirm isolation before entering the space.",
    tags=("water", "leak"),
    steps=(
        Step(
            id="scope",
            phase="Assess",
            title="What is underneath it?",
            instruction="A leak is detected. What determines how urgently you act?",
            options=(
                _o("whats-below", "What the water can reach — electrical plant, IT, occupied space",
                   "Correct. Severity is about consequence, not volume. A slow drip "
                   "over a switchboard outranks a fast leak over a drain."),
                _o("rate", "The flow rate",
                   "Useful for sizing the response, but a large leak into a plant "
                   "room drain may matter less than a small one over a panel."),
                _o("age", "How long it has been leaking",
                   "Informative, not decisive. It tells you about damage already "
                   "done rather than what to do now."),
                _o("source", "Which pipe it is",
                   "That comes next. It does not set the urgency."),
            ),
            correct="whats-below",
            hint="Severity is about consequence, not volume.",
            rationale=("Water is only as dangerous as what it lands on. That "
                       "judgement sets everything that follows."),
        ),
        Step(
            id="isolate",
            phase="Act",
            title="Isolate",
            instruction=("Water is reaching a floor above an electrical room. What "
                         "is your first physical action?"),
            options=(
                _o("isolate-upstream", "Close the nearest upstream isolation valve",
                   "Correct. Stop the source. Everything else is cleanup that keeps "
                   "getting worse while the water runs."),
                _o("mop", "Contain and absorb the water",
                   "Containment matters, but doing it while the leak runs is "
                   "bailing a boat without plugging the hole."),
                _o("power-off", "De-energise the electrical room below",
                   "Sometimes right, and a big decision. Try stopping the water "
                   "first — it may make this unnecessary."),
                _o("photo", "Photograph the leak for the insurance record",
                   "Not now. The record can be reconstructed; the damage cannot be "
                   "undone."),
            ),
            correct="isolate-upstream",
            hint="What stops the situation getting worse while you think?",
            rationale=("Isolation converts a developing incident into a static one, "
                       "which is what gives you time to make good decisions."),
        ),
        Step(
            id="verify-flow",
            phase="Verify",
            title="Confirm it actually stopped",
            instruction="The valve is closed. How do you confirm the leak has stopped?",
            options=(
                _o("flow-zero", "Watch the flow meter read zero and the detector reset",
                   "Correct. Two independent confirmations, both from instruments "
                   "that were already telling you the truth."),
                _o("look", "Look at the leak site",
                   "Residual water keeps dripping for a while after isolation. The "
                   "eye cannot separate that from a continuing leak."),
                _o("assume", "Assume it stopped; the valve is shut",
                   "Valves pass. That is exactly why the flow meter exists."),
                _o("wait", "Wait an hour and re-check",
                   "Too slow with water above an electrical room, and it does not "
                   "tell you anything the meter cannot say now."),
            ),
            correct="flow-zero",
            hint="You already have two instruments watching this.",
            rationale=("Zero flow with a reset detector is positive confirmation. "
                       "A dry-looking floor is not."),
        ),
    ),
)

TANK_LOW_LEVEL = Procedure(
    id="tank-low-level",
    title="Tank level low",
    summary="A storage tank has fallen below its low-level threshold.",
    domain="water",
    behavior_globs=("cfp.tank_low_level",),
    difficulty="easy",
    est_minutes=5,
    tags=("water", "storage"),
    steps=(
        Step(
            id="consume-or-leak",
            phase="Assess",
            title="Consumption or loss?",
            instruction="The tank is below its low threshold. What do you check first?",
            options=(
                _o("rate", "The rate of fall against normal consumption",
                   "Correct. Normal drawdown and a leak look identical at one point "
                   "in time and completely different as a rate."),
                _o("refill", "Start the refill immediately",
                   "You may well need to — but refilling a leaking tank hides the "
                   "leak and wastes the water."),
                _o("alarm", "Verify the level sensor is accurate",
                   "Worth doing if the reading is implausible. Not the first move "
                   "on a plausible one."),
                _o("notify", "Notify the supervisor",
                   "Fine to do in parallel; it is not a diagnostic step."),
            ),
            correct="rate",
            hint="One point on a graph tells you much less than its slope.",
            rationale=("A fall faster than consumption explains means loss. That "
                       "single comparison sets the whole response."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Respond",
            instruction=("The fall matches normal consumption — the auto-refill did "
                         "not trigger. What do you do?"),
            options=(
                _o("manual-refill", "Refill manually and raise a job on the refill control",
                   "Correct. Restore the level now, and fix the control that should "
                   "have done it so this is not a daily manual task."),
                _o("refill-only", "Refill manually and close the task",
                   "The level comes back and the fault stays. You have volunteered "
                   "to do this again tomorrow."),
                _o("control-only", "Raise a job on the refill control and leave the level",
                   "The control gets fixed eventually and the tank runs dry "
                   "meanwhile."),
                _o("threshold", "Lower the low-level threshold to stop the alarm",
                   "This silences the instrument that is working correctly. Never "
                   "the answer."),
            ),
            correct="manual-refill",
            hint="Two things are wrong here, not one.",
            rationale=("The level is the symptom, the failed refill control is the "
                       "fault. Addressing only one guarantees a repeat."),
        ),
    ),
)

# ── Cooling and air ─────────────────────────────────────────────────────
CHILLER_COP_DROP = Procedure(
    id="chiller-cop-drop",
    title="Chiller efficiency below baseline",
    summary=("The chiller is delivering its duty but consuming markedly more energy "
             "than its learned baseline for these conditions."),
    domain="cooling",
    behavior_globs=("cfp.chiller_cop_baseline",),
    difficulty="medium",
    est_minutes=9,
    tags=("cooling", "efficiency", "chiller"),
    steps=(
        Step(
            id="real",
            phase="Assess",
            title="Is the drop real?",
            instruction=("COP has fallen against baseline. What could make that "
                         "reading misleading?"),
            options=(
                _o("conditions", "Ambient and load conditions differing from the baseline period",
                   "Correct. COP is condition-dependent. A hot day at part load "
                   "legitimately produces a lower number."),
                _o("age", "The chiller is simply older",
                   "Degradation is real but gradual. It does not explain a step "
                   "change against a recent baseline."),
                _o("sensor", "A drifting power meter",
                   "Possible, and worth ruling out — but conditions explain far "
                   "more of these alerts than instrumentation does."),
                _o("nothing", "Nothing — a baseline comparison is definitionally correct",
                   "A baseline is only valid within the conditions it was learned "
                   "in. Treating it as absolute produces confident wrong answers."),
            ),
            correct="conditions",
            hint="This number is a ratio measured under conditions that move.",
            rationale=("Normalising for ambient and load is what separates a real "
                       "efficiency loss from a hot afternoon."),
        ),
        Step(
            id="cause",
            phase="Diagnose",
            title="Where does the efficiency go?",
            instruction=("Conditions are comparable and the drop is real. What is the "
                         "most common cause?"),
            options=(
                _o("heat-exchange", "Fouled condenser or evaporator surfaces",
                   "Correct. Degraded heat exchange makes the compressor work "
                   "harder for the same duty — the classic COP decay."),
                _o("refrigerant", "Low refrigerant charge",
                   "It does reduce capacity and efficiency, and usually announces "
                   "itself with superheat and temperature signatures too."),
                _o("compressor", "Compressor wear",
                   "Real, but slower and much more expensive to conclude. Rule out "
                   "the cheap causes first."),
                _o("controls", "Control setpoint drift",
                   "Worth a look. It more often changes what the plant does than "
                   "how efficiently it does it."),
            ),
            correct="heat-exchange",
            hint="Efficiency loss usually means heat is not moving as easily as it did.",
            rationale=("Fouling raises condensing pressure and lowers evaporating "
                       "pressure, and the compressor pays for both."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Act proportionally",
            instruction=("Condenser approach temperature is well above commissioning "
                         "figures. What do you raise?"),
            options=(
                _o("clean", "A condenser clean, scheduled at the next low-load window",
                   "Correct. It matches the diagnosis, and scheduling it off-peak "
                   "avoids trading efficiency for a capacity shortfall."),
                _o("clean-now", "A condenser clean, immediately",
                   "Right work, wrong timing — taking a chiller out at peak load to "
                   "save energy can cost you the cooling."),
                _o("replace", "A compressor replacement quote",
                   "Expensive, and unsupported by this evidence."),
                _o("monitor", "Nothing; monitor for another month",
                   "The plant burns the extra energy every day of that month, and "
                   "fouling does not improve on its own."),
            ),
            correct="clean",
            hint="Right action, and the right moment for it.",
            rationale=("Approach temperature is the direct fouling signature, and a "
                       "low-load window is when the plant can spare the machine."),
        ),
    ),
)

FILTER_CLOGGED = Procedure(
    id="filter-clogged",
    title="Air filter differential high",
    summary="Pressure drop across a filter bank has exceeded its change threshold.",
    domain="air",
    behavior_globs=("cfp.filter_clogged",),
    difficulty="easy",
    est_minutes=5,
    tags=("air", "ahu", "maintenance"),
    steps=(
        Step(
            id="confirm",
            phase="Assess",
            title="Confirm the reading",
            instruction=("Differential pressure across the filter bank is above "
                         "threshold. What confirms it is genuinely loaded?"),
            options=(
                _o("trend", "A gradual rise in differential over weeks",
                   "Correct. Loading is a slow ramp. That shape is the signature "
                   "that separates it from anything else."),
                _o("spike", "A sudden jump in differential",
                   "A step change suggests a blocked damper or a collapsed filter, "
                   "which is a different fault with a different fix."),
                _o("visual", "A visual check of the filter faces",
                   "Useful confirmation, and it misses loading in the depth of the "
                   "media where most of it happens."),
                _o("hours", "Run hours since the last change",
                   "A scheduling input, not evidence. Filters load at very "
                   "different rates depending on what the air carries."),
            ),
            correct="trend",
            hint="Loading happens gradually. What shape does that make on a chart?",
            rationale=("A steady ramp is loading. A step is an obstruction, and "
                       "changing filters would not fix it."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Change and record",
            instruction="The trend confirms loading. What does a complete job include?",
            options=(
                _o("change-record", "Change the filters and record the new clean differential",
                   "Correct. The clean baseline is what makes the next alert "
                   "meaningful — without it the threshold slowly loses calibration."),
                _o("change", "Change the filters",
                   "Half the job. The next person inherits a threshold nobody "
                   "re-baselined."),
                _o("raise", "Raise the alarm threshold to stop the nuisance alert",
                   "This disables the instrument rather than fixing the plant."),
                _o("fan", "Increase fan speed to restore airflow",
                   "This masks the restriction and spends fan energy to do it."),
            ),
            correct="change-record",
            hint="What does the next alert depend on?",
            rationale=("Recording the clean differential re-establishes the baseline "
                       "the threshold is measured from."),
        ),
    ),
)

HVAC_ZONE_TEMP = Procedure(
    id="hvac-zone-temp",
    title="Zone temperature out of range",
    summary=("A zone has drifted outside its setpoint band, by threshold, "
             "statistical or physics-model detection."),
    domain="hvac",
    behavior_globs=("hvac.*",),
    difficulty="medium",
    est_minutes=7,
    tags=("hvac", "comfort", "zone"),
    steps=(
        Step(
            id="sensor-or-real",
            phase="Assess",
            title="Real, or instrumentation?",
            instruction=("A zone reads out of range. How do you decide whether the "
                         "temperature is genuinely wrong?"),
            options=(
                _o("neighbours", "Compare with neighbouring zones and the return air temperature",
                   "Correct. A single sensor cannot validate itself; its neighbours "
                   "and the return air can."),
                _o("believe", "Trust the sensor — that is what it is for",
                   "Sensors fail, and a failed one sends the plant chasing a "
                   "temperature that does not exist."),
                _o("replace", "Replace the sensor",
                   "Expensive as a first move, and it discards the evidence that "
                   "would have told you whether it was faulty."),
                _o("setpoint", "Adjust the setpoint until the alarm clears",
                   "This silences the alert and leaves the zone wrong."),
            ),
            correct="neighbours",
            hint="One instrument cannot check itself.",
            rationale=("Agreement with neighbours means the temperature is real; "
                       "disagreement points at the sensor."),
        ),
        Step(
            id="delivery",
            phase="Diagnose",
            title="Is conditioned air arriving?",
            instruction=("Neighbouring zones agree — the zone really is too warm. "
                         "What do you check next?"),
            options=(
                _o("airflow", "Airflow to the zone: damper position, VAV box, supply temperature",
                   "Correct. The zone is warm because it is not receiving cooling, "
                   "and that path is where cooling gets lost."),
                _o("chiller", "Whether the chiller is running",
                   "If the chiller were down, the neighbours would be warm too. "
                   "They are not."),
                _o("occupancy", "Whether occupancy is higher than usual",
                   "A real contributor, and it should still be within the design "
                   "margin. Check delivery first."),
                _o("windows", "Whether a window or door is open",
                   "Worth a look on the walk, but rarely the whole story on a "
                   "detected drift."),
            ),
            correct="airflow",
            hint="One zone is affected. What is unique to that zone?",
            rationale=("A single warm zone with healthy neighbours localises the "
                       "fault to that zone's delivery path."),
        ),
        Step(
            id="fix",
            phase="Act",
            title="Restore control",
            instruction="The VAV damper is stuck at minimum. What is the correct fix?",
            options=(
                _o("free-verify", "Free or replace the actuator, then verify the zone recovers",
                   "Correct. Fix the mechanism and confirm the zone actually comes "
                   "back — an actuator that moves is not yet a zone in range."),
                _o("override", "Command the damper open in the BMS",
                   "A temporary workaround. It leaves a stuck actuator and an "
                   "override that somebody will find confusing in six months."),
                _o("boost", "Lower the supply air temperature to compensate",
                   "This overcools every other zone on the same AHU to fix one."),
                _o("actuator-only", "Replace the actuator and close the task",
                   "Close, but unverified. Confirm the zone recovers before you "
                   "call it fixed."),
            ),
            correct="free-verify",
            hint="Fixing the mechanism and fixing the zone are two claims.",
            rationale=("Verification is what separates a repair from a hopeful "
                       "part swap."),
        ),
    ),
)

# ── Rotating plant ──────────────────────────────────────────────────────
PUMP_VIBRATION = Procedure(
    id="pump-vibration",
    title="Pump vibration above baseline",
    summary="Vibration on a pump has risen significantly against its learned baseline.",
    domain="mechanical",
    behavior_globs=("cfp.pump_vibration_baseline",),
    difficulty="hard",
    est_minutes=9,
    safety="Rotating plant. Do not attempt physical inspection while running.",
    tags=("mechanical", "vibration", "pump"),
    steps=(
        Step(
            id="trend-shape",
            phase="Assess",
            title="Read the trend",
            instruction=("Vibration is above baseline. What does the SHAPE of the "
                         "rise tell you?"),
            options=(
                _o("gradual-step", "Gradual means wear; a step means something changed or broke",
                   "Correct. That distinction drives everything — wear is planned "
                   "work, a step change may be an immediate stop."),
                _o("amplitude", "Only the amplitude matters",
                   "Amplitude tells you how bad. The shape tells you what, and what "
                   "is the actionable half."),
                _o("nothing", "The shape tells you nothing without a spectrum",
                   "A spectrum is better, and the trend shape is available right "
                   "now and already narrows the field."),
                _o("duration", "How long it has been elevated",
                   "Useful context, secondary to the shape."),
            ),
            correct="gradual-step",
            hint="Wear and breakage do not look the same over time.",
            rationale=("Gradual rise is degradation you can schedule around. A step "
                       "is an event, and it may not be safe to keep running."),
        ),
        Step(
            id="cause",
            phase="Diagnose",
            title="Narrow it down",
            instruction=("The rise was a step change two days ago, then stable. Most "
                         "likely cause?"),
            options=(
                _o("mechanical", "Something changed mechanically — coupling, mounting, or debris",
                   "Correct. A step and then a new stable level is a changed "
                   "condition, not a progressing failure."),
                _o("bearing", "Bearing degradation",
                   "That is usually a rising trend with characteristic frequencies, "
                   "not a step to a new plateau."),
                _o("cavitation", "Cavitation from suction conditions",
                   "Plausible — and it normally tracks operating point rather than "
                   "stepping once and staying."),
                _o("sensor", "Accelerometer mounting has come loose",
                   "A genuinely good thought and a real cause of step changes. Rule "
                   "it out early — but it usually reads erratic rather than stable."),
            ),
            correct="mechanical",
            hint="It stepped once and then stayed. What kind of cause does that?",
            rationale=("Step-then-stable means the machine is in a new steady state. "
                       "Progressive failures do not plateau."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Decide on running",
            instruction=("Vibration is elevated but stable and below the trip limit. "
                         "Duty is required. What do you do?"),
            options=(
                _o("run-monitor", "Keep it running with increased monitoring and schedule an inspection",
                   "Correct. Below trip and stable justifies continued operation; "
                   "the tighter monitoring is what makes that defensible."),
                _o("stop", "Stop it now",
                   "Over-cautious given stable readings below the limit, and it "
                   "costs the duty this plant exists to provide."),
                _o("run", "Keep running, no change",
                   "This ignores a known changed condition. If it progresses, "
                   "nobody finds out until the trip."),
                _o("standby", "Switch to the standby pump and leave this one stopped",
                   "Reasonable if a standby exists, and it spends your redundancy "
                   "on a machine that is still within limits."),
            ),
            correct="run-monitor",
            hint="Stable and below limit. What justifies continuing — and what makes that safe?",
            rationale=("Continued operation plus heightened monitoring balances duty "
                       "against risk, and the inspection addresses the cause."),
        ),
    ),
)

# ── Life safety, network, security ──────────────────────────────────────
SMOKE_ALARM = Procedure(
    id="smoke-alarm",
    title="Smoke detection activated",
    summary="A smoke detector has activated. Life safety takes precedence over plant.",
    domain="fire",
    behavior_globs=("cfp.smoke_alarm",),
    difficulty="expert",
    est_minutes=6,
    safety="LIFE SAFETY. Never silence or reset a fire alarm before the zone is confirmed clear.",
    tags=("fire", "life-safety", "critical"),
    steps=(
        Step(
            id="first",
            phase="Act",
            title="First action",
            instruction="A smoke detector has activated in an occupied zone. What comes first?",
            options=(
                _o("evacuate", "Follow the evacuation procedure and confirm the alarm reached the fire panel",
                   "Correct. Life safety first, always. Every diagnostic question "
                   "can be asked after people are safe."),
                _o("investigate", "Go to the zone to check whether it is a real fire",
                   "This is how people are hurt. Investigation happens under the "
                   "fire procedure, not instead of it."),
                _o("silence", "Silence the sounder while you investigate",
                   "Never. Silencing before a zone is confirmed clear has killed "
                   "people, and it is why the option is here."),
                _o("cctv", "Check the cameras covering that zone",
                   "Useful information, and not a substitute for the procedure. Do "
                   "it in parallel, not instead."),
            ),
            correct="evacuate",
            hint="There is only ever one right first action on a fire alarm.",
            rationale=("The fire procedure exists precisely so this decision is not "
                       "made under pressure by one person."),
        ),
        Step(
            id="after",
            phase="Verify",
            title="After the zone is clear",
            instruction=("The fire service has confirmed a false activation from dust "
                         "during ceiling work. What now?"),
            options=(
                _o("reset-record", "Reset under the fire procedure and record the cause against the detector",
                   "Correct. The reset is procedural, and recording the cause is "
                   "what stops the same detector being written off as unreliable."),
                _o("reset", "Reset the panel",
                   "Incomplete. A false activation with no recorded cause becomes "
                   "an unexplained one in the log."),
                _o("disable", "Disable that detector until the work finishes",
                   "Only ever with a formal impairment process, and never on an "
                   "operator's own initiative."),
                _o("nothing", "Leave the panel in alarm; it will be reset at handover",
                   "This leaves the system unable to report the next event."),
            ),
            correct="reset-record",
            hint="The reset is procedural. What else does the next person need?",
            rationale=("A recorded cause turns a false alarm into evidence rather "
                       "than noise, and protects the detector's credibility."),
        ),
    ),
)

HEARTBEAT_LOSS = Procedure(
    id="heartbeat-loss",
    title="Device heartbeat lost",
    summary="A monitored device has stopped reporting. The plant may be fine; the visibility is not.",
    domain="network",
    behavior_globs=("cfp.heartbeat_loss",),
    difficulty="medium",
    est_minutes=6,
    tags=("network", "telemetry"),
    steps=(
        Step(
            id="scope",
            phase="Assess",
            title="One device or many?",
            instruction="A device has stopped reporting. What do you establish first?",
            options=(
                _o("blast", "Whether other devices on the same segment also went quiet",
                   "Correct. One device is a device problem; several at once is a "
                   "network or power problem, and they are different jobs."),
                _o("restart", "Restart the device",
                   "You cannot restart what you cannot reach, and it discards the "
                   "evidence of why it stopped."),
                _o("cable", "Check its network cable",
                   "That is the single-device branch of the answer. Establish which "
                   "branch you are on first."),
                _o("ignore", "Wait — heartbeats are often briefly lost",
                   "Brief losses do happen and are usually suppressed before they "
                   "raise anything. This one was raised."),
            ),
            correct="blast",
            hint="Scope before cause. How many things are affected?",
            rationale=("The blast radius picks your entire diagnostic path, and it "
                       "costs one glance at the other devices."),
        ),
        Step(
            id="assume",
            phase="Diagnose",
            title="What you must not assume",
            instruction=("Only this device is affected. What is the dangerous "
                         "assumption here?"),
            options=(
                _o("plant-fine", "Assuming the plant it monitors is still healthy",
                   "Correct. Losing the heartbeat means losing the ability to know. "
                   "Silence is not the same as good news."),
                _o("device-dead", "Assuming the device has failed",
                   "A reasonable working hypothesis, and easily tested."),
                _o("network", "Assuming it is a network fault",
                   "Also reasonable, and the scope check already narrowed it."),
                _o("temporary", "Assuming it is temporary",
                   "Worth being wary of, and it is the less dangerous of the "
                   "assumptions on offer."),
            ),
            correct="plant-fine",
            hint="What did you actually lose when the heartbeat stopped?",
            rationale=("The fault is loss of visibility. Until it is restored, the "
                       "monitored plant needs checking by other means."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Restore both",
            instruction="How do you close this properly?",
            options=(
                _o("both", "Restore the device AND verify the plant it monitors was healthy throughout",
                   "Correct. Fixing the telemetry does not retroactively tell you "
                   "what happened during the blind window."),
                _o("device", "Restore the device and close the task",
                   "This leaves the blind period unexamined. Anything could have "
                   "happened in it."),
                _o("plant", "Check the plant and close the task",
                   "Then you are blind again the moment you walk away."),
                _o("replace", "Replace the device",
                   "Possibly necessary; not the whole close-out either way."),
            ),
            correct="both",
            hint="There is a window of time nobody was watching.",
            rationale=("Two things were lost — the device and the record. Both need "
                       "restoring before the task is genuinely closed."),
        ),
    ),
)

DOOR_FORCED = Procedure(
    id="door-forced",
    title="Door forced or repeated access denial",
    summary="An access-controlled door was forced, or has denied the same credential repeatedly.",
    domain="security",
    behavior_globs=("cfp.door_forced", "cfp.repeated_deny"),
    difficulty="medium",
    est_minutes=5,
    safety="Do not confront. Observe and report.",
    tags=("security", "access"),
    steps=(
        Step(
            id="verify",
            phase="Assess",
            title="Event or fault?",
            instruction=("A door reports forced. What distinguishes a security event "
                         "from a hardware fault?"),
            options=(
                _o("correlate", "Correlate with camera footage and the door's recent fault history",
                   "Correct. A failing door contact reports forced repeatedly with "
                   "nothing on camera — a pattern the footage settles immediately."),
                _o("attend", "Attend the door immediately",
                   "Sometimes right, and not before you know what you are walking "
                   "into. Look first."),
                _o("lock", "Lock down the area",
                   "Disproportionate for a single event, and disruptive if it turns "
                   "out to be a faulty contact."),
                _o("log", "Log it and review at the end of shift",
                   "Too slow if it is real. The correlation takes a minute."),
            ),
            correct="correlate",
            hint="One of these causes leaves evidence on camera and the other does not.",
            rationale=("Correlation separates a real entry from a failing sensor "
                       "faster and more safely than attending does."),
        ),
        Step(
            id="act",
            phase="Act",
            title="Respond",
            instruction=("Footage shows nothing and the door has reported forced "
                         "eleven times this week. What is this?"),
            options=(
                _o("hardware", "A hardware fault — raise a door maintenance job",
                   "Correct. Repeated alarms with no footage is a failing contact "
                   "or misaligned door, not eleven intrusions."),
                _o("security", "A security incident to escalate",
                   "The evidence does not support it, and escalating noise devalues "
                   "the escalations that matter."),
                _o("disable", "Disable the forced-door alarm on that door",
                   "This removes the detection instead of fixing the door."),
                _o("monitor", "Keep monitoring for another week",
                   "The alarm keeps crying wolf and people learn to ignore it."),
            ),
            correct="hardware",
            hint="Eleven times, no footage. What does that pattern actually mean?",
            rationale=("A repeating alarm with no corroborating evidence is an "
                       "instrumentation fault, and leaving it erodes trust in every "
                       "other door."),
        ),
    ),
)


# ── Registry ────────────────────────────────────────────────────────────
#
# Declaration order IS match order — most specific first. `hvac-zone-temp` uses
# a wildcard glob, so it sits at the end where it cannot shadow a more precise
# procedure.
PROCEDURES: tuple[Procedure, ...] = (
    UPS_ON_BATTERY,
    TRANSFORMER_OVER_TEMP,
    WATER_LEAK,
    TANK_LOW_LEVEL,
    CHILLER_COP_DROP,
    FILTER_CLOGGED,
    PUMP_VIBRATION,
    SMOKE_ALARM,
    HEARTBEAT_LOSS,
    DOOR_FORCED,
    HVAC_ZONE_TEMP,
)

_BY_ID = {p.id: p for p in PROCEDURES}


def get(scenario_id: str) -> Procedure | None:
    return _BY_ID.get((scenario_id or "").strip())


def all_procedures() -> tuple[Procedure, ...]:
    return PROCEDURES


def scenario_id_for_behavior(behavior_id: str) -> str:
    """Which procedure teaches this behaviour's fault. `""` if none does.

    An unmatched behaviour is not an error: the task is still raised and still
    dispatched, the operator just gets no guided procedure with it. Failing the
    whole dispatch because content is missing would be a much worse trade.
    """
    behavior = (behavior_id or "").strip()
    if not behavior:
        return ""
    for procedure in PROCEDURES:
        for glob in procedure.behavior_globs:
            if fnmatch.fnmatch(behavior, glob):
                return procedure.id
    return ""


def for_behavior(behavior_id: str) -> Procedure | None:
    return get(scenario_id_for_behavior(behavior_id))
