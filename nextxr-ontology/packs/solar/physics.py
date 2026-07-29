"""
physics.py — the solar PV array forward model.

Implements the platform's machine-twin physics contract (see packs/turbine/physics.py):

    SIGNALS / UNITS      the signal catalogue (imported from signals.py)
    redlines             hard limits for diagnostics + prediction
    SolarPhysics         init_state / forward / inject / residuals / health_index

WHAT MAKES THIS MODEL DIFFERENT FROM THE OTHER PACKS
----------------------------------------------------
A turbine's output is commanded — you set a throttle and it responds. A PV array's
output is DICTATED BY THE WEATHER, and that inverts the diagnostic problem: you
cannot tell whether an array is healthy by looking at its output at all. 400 kW from
a 500 kW array is excellent at 8 a.m. and alarming at noon.

So this model has two halves, and the separation is the whole point:

  1. An IDEAL model — the De Soto single-diode curve (desoto.py) driven by measured
     irradiance and cell temperature. What a clean array SHOULD produce right now.
  2. DEGRADATION accumulators — soiling, per-string shading, PID/shunt decay,
     resistive loss — that perturb the ideal to produce what the array DOES.

The residual between them (§4.2's ΔP) is the only quantity that carries diagnostic
information, and every maintenance trigger in the specification is a condition on it
or on its shape across strings.

WHY EACH FAULT PERTURBS A DIFFERENT PARAMETER
--------------------------------------------
This is what allows §4.2's three signatures to be told apart rather than all
presenting as "underperforming":

    soiling          scales I_L (photocurrent) uniformly across a zone
                     -> current down, voltage NORMAL, all strings equally
    shading / bypass  removes VOLTAGE from one string (diode bypasses a
                     substring), current unaffected
                     -> step drop in V_dc on ONE string, I_dc constant
    PID / delamination decays R_sh_ref secularly
                     -> fill factor erodes, V_oc sags, over months
    resistive loss    raises R_s
                     -> fill factor erodes with V_oc INTACT

Those are not arbitrary: they are the actual physical mechanisms, which is why the
residual SHAPE identifies the root cause and a single scalar "performance ratio"
never could.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from packs._core.physics import (
    clamp,
    first_order_lag,
    jitter,
    margin_hi,
    margin_lo,
    worst_health,
)

from . import desoto as D
from .signals import (
    DERIVED_SIGNALS,
    INVERTER_SIGNALS,
    METER_SIGNALS,
    STRING_SIGNALS,
    WEATHER_SIGNALS,
)

# The pack's flat signal view, for the SPEC/runtime.
SIGNALS = {
    "poa": WEATHER_SIGNALS["poa_irradiance"],
    "ghi": WEATHER_SIGNALS["ghi"],
    "module_temp": WEATHER_SIGNALS["module_temp"],
    "cell_temp": WEATHER_SIGNALS["cell_temp"],
    "ambient_temp": WEATHER_SIGNALS["ambient_temp"],
    "wind": WEATHER_SIGNALS["wind_speed"],
    "dc_voltage": INVERTER_SIGNALS["dc_voltage"],
    "dc_current": INVERTER_SIGNALS["dc_current"],
    "dc_power": INVERTER_SIGNALS["dc_power"],
    "ac_power": INVERTER_SIGNALS["ac_power"],
    "efficiency": INVERTER_SIGNALS["efficiency"],
    "heatsink_temp": INVERTER_SIGNALS["heatsink_temp"],
    "frequency": INVERTER_SIGNALS["frequency"],
    "state": INVERTER_SIGNALS["operating_state"],
    "string_current": STRING_SIGNALS["string_current"],
    "string_voltage": STRING_SIGNALS["string_voltage"],
    "modelled_dc": DERIVED_SIGNALS["modelled_dc_power"],
    "delta_p": DERIVED_SIGNALS["delta_p"],
    "delta_p_pct": DERIVED_SIGNALS["delta_p_pct"],
    "pr": DERIVED_SIGNALS["performance_ratio"],
    "rsh": DERIVED_SIGNALS["r_shunt_ref"],
    "ff": DERIVED_SIGNALS["fill_factor"],
    "imbalance": DERIVED_SIGNALS["current_imbalance"],
    "facility_load": METER_SIGNALS["facility_load"],
    "net_grid": METER_SIGNALS["net_power"],
}


@dataclass
class Redlines:
    """Hard limits. Sourced from the physics and from IEC 62109 / IEC 61727 rather
    than chosen for demo convenience."""
    # A 1500 V system's maximum permissible open-circuit voltage. Exceeding it
    # breaks the inverter's insulation rating — a safety limit, not a performance one.
    dc_voltage_max: float = 1500.0
    # Inverter derates above this and shuts down not far beyond it.
    heatsink_temp: float = 85.0
    # Cell temperature at which output loss becomes severe (~ -0.35 %/K from 25 °C).
    cell_temp: float = 85.0
    # §4.2's soiling trigger: ΔP below -12 % across a zone.
    delta_p_pct: float = -12.0
    # A well-run commercial array holds PR above ~0.75; below 0.70 something is wrong.
    performance_ratio_min: float = 0.70
    # Spread across strings in one combiner. Above this, one string is misbehaving.
    current_imbalance_pct: float = 8.0
    # IEC 62109 insulation-resistance floor before a DC ground fault is declared.
    isolation_resistance_min: float = 1_000_000.0
    # Grid limits (IEC 61727 / IEEE 1547).
    frequency_min: float = 47.5
    frequency_max: float = 51.5
    # Fill factor below this indicates serious resistive or shunt degradation.
    fill_factor_min: float = 0.62


redlines = Redlines()

# Named faults the twin can inject — the demo/verification surface for §4.2, and
# what the Multiplayer Training Labs (§5.3) run against.
FAULTS = {
    "none": "Healthy array",
    "soiling": "Dust/soiling build-up — uniform current suppression across a zone",
    "shading": "Localised shading / bypass-diode activation on one string",
    "pid": "Potential-induced degradation — shunt resistance decay",
    "delamination": "Module delamination — shunt decay plus optical loss",
    "resistive": "Connector/busbar resistive loss — series resistance rise",
    "string_open": "String fuse blown / open circuit — one string contributes nothing",
    "inverter_derate": "Inverter thermal derate — AC output clipped below DC available",
    "sensor_fault": "Pyranometer or RTD failure — baseline becomes untrustworthy",
}

# Inverter efficiency curve. A real inverter is poor at very low load (its own
# consumption dominates), peaks near 30-50 % of rating, and falls slightly at full
# load. Modelling it matters because a naive constant efficiency would make every
# morning and evening look like an underperformance fault.
_EFF_CURVE = ((0.00, 0.00), (0.02, 0.72), (0.05, 0.900), (0.10, 0.958),
              (0.20, 0.977), (0.30, 0.982), (0.50, 0.983), (0.75, 0.979),
              (1.00, 0.972))


def inverter_efficiency(load_fraction: float) -> float:
    """Piecewise-linear interpolation of the efficiency curve."""
    x = clamp(load_fraction, 0.0, 1.0)
    for (x0, y0), (x1, y1) in zip(_EFF_CURVE, _EFF_CURVE[1:], strict=False):
        if x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return _EFF_CURVE[-1][1]


@dataclass
class StringState:
    """One PV string. Per-string state is what makes §4.2 diagnosable."""
    string_id: str
    modules_in_series: int = 20
    # 1.0 = clean. Scales photocurrent — the soiling and shading mechanism.
    soiling: float = 1.0
    # Fraction of the string's modules bypassed by their diodes (shading). Removes
    # VOLTAGE, not current, which is exactly the §4.2 signature.
    bypassed_fraction: float = 0.0
    # Secular shunt decay, 1.0 = pristine. The PID/delamination mechanism.
    rsh_factor: float = 1.0
    # Series-resistance multiplier, 1.0 = as-built. Connector/busbar degradation.
    rs_factor: float = 1.0
    open_circuit: bool = False        # blown fuse
    # Cumulative soiling since the last wash, for the cleaning-ticket narrative.
    days_since_clean: float = 0.0


@dataclass
class SolarState:
    """Whole-array state."""
    # Plant configuration
    strings: list[StringState] = field(default_factory=list)
    modules_in_series: int = 20
    inverter_rating_w: float = 100_000.0
    module_pmp_stc: float = D.REFERENCE_MODULE_PMP_STC

    # Environment. Driven by measurements when they exist; simulated otherwise.
    poa: float = 0.0
    ghi: float = 0.0
    ambient_temp: float = 22.0
    wind_speed: float = 2.0
    module_temp: float = 22.0
    cell_temp: float = 22.0
    # Simulated clock, seconds since midnight local. Only used when the twin has no
    # measured irradiance — a demo/training twin (§5.3) rather than a live site.
    time_of_day_s: float = 9.0 * 3600.0
    day_of_year: int = 172            # summer solstice — the useful default for a demo
    latitude: float = 13.08           # Chennai, matching the platform's other demos
    cloud: float = 0.0                # 0 = clear, 1 = fully overcast

    # Inverter thermal state (lags, so it is carried between ticks)
    heatsink_temp: float = 25.0
    operating_state: int = 1          # sleeping until irradiance arrives
    derate_factor: float = 1.0

    # Facility side (§2.1 meter + CT clamps)
    facility_load_kw: float = 45.0

    # Injected fault
    fault: str = "none"
    fault_severity: float = 0.0
    # Whether measured environment is being supplied. When True the diurnal
    # simulator is bypassed — a live site must never have its real irradiance
    # overwritten by a model.
    measured_env: bool = False

    _rng: random.Random | None = None

    def rng(self) -> random.Random:
        """Seeded, so a run is reproducible and a projection is comparable to the
        live twin it branched from."""
        if self._rng is None:
            self._rng = random.Random(20260726)
        return self._rng


def build_strings(count: int = 12, modules_in_series: int = 20,
                 zone: int = 1, inverter: int = 1) -> list[StringState]:
    """Strings named per §3 Step 1's hierarchical tagging, so a state object's
    string ids match both the 3-D scene tree and the combiner's point map."""
    return [
        StringState(string_id=f"Zone_{zone:02d}_Inverter_{inverter:02d}_String_{i:02d}",
                    modules_in_series=modules_in_series)
        for i in range(1, count + 1)
    ]


class SolarPhysics:
    """The array forward model."""

    def __init__(self, module: D.ModuleParams = D.REFERENCE_MODULE):
        self.module = module

    # ── State ───────────────────────────────────────────────────────────────

    def init_state(self, *, strings: int = 12, modules_in_series: int = 20,
                   inverter_rating_w: float = 100_000.0) -> SolarState:
        state = SolarState(
            strings=build_strings(strings, modules_in_series),
            modules_in_series=modules_in_series,
            inverter_rating_w=inverter_rating_w,
            module_pmp_stc=D.REFERENCE_MODULE_PMP_STC,
        )
        # A plausible mid-life array rather than a pristine one: every string carries
        # a little soiling and a little spread. A twin that starts perfect makes the
        # first real reading look like a fault.
        rng = state.rng()
        for s in state.strings:
            s.soiling = 1.0 - rng.uniform(0.01, 0.04)
            s.days_since_clean = rng.uniform(5.0, 40.0)
            s.rsh_factor = 1.0 - rng.uniform(0.0, 0.03)
            s.rs_factor = 1.0 + rng.uniform(0.0, 0.04)
        return state

    def nameplate_w(self, state: SolarState) -> float:
        """Installed DC capacity — the denominator of performance ratio."""
        modules = sum(s.modules_in_series for s in state.strings)
        return modules * state.module_pmp_stc

    # ── Environment ─────────────────────────────────────────────────────────

    def _advance_environment(self, state: SolarState, dt: float) -> None:
        """Simulate a diurnal irradiance cycle. Skipped when measurements exist.

        The `measured_env` guard is important: on a live site the pyranometer is the
        authority and overwriting it with a clear-sky model would make every residual
        meaningless. This path exists for the demo twins and for §5.3's training labs,
        where there is no hardware at all.
        """
        if state.measured_env:
            return

        state.time_of_day_s = (state.time_of_day_s + dt) % 86400.0
        rng = state.rng()

        # Solar declination + hour angle → elevation. A real clear-sky model, so the
        # simulated day has the right shape and length for the latitude and season.
        declination = 23.45 * math.sin(math.radians(360.0 * (284 + state.day_of_year) / 365.0))
        hour_angle = 15.0 * (state.time_of_day_s / 3600.0 - 12.0)
        elevation = math.degrees(math.asin(clamp(
            math.sin(math.radians(state.latitude)) * math.sin(math.radians(declination))
            + math.cos(math.radians(state.latitude)) * math.cos(math.radians(declination))
            * math.cos(math.radians(hour_angle)), -1.0, 1.0)))

        if elevation <= 0.5:
            clear_sky = 0.0
        else:
            # Air mass (Kasten-Young) then a simple atmospheric transmittance.
            air_mass = 1.0 / (math.sin(math.radians(elevation))
                              + 0.50572 * (elevation + 6.07995) ** -1.6364)
            clear_sky = 1100.0 * (0.7 ** (air_mass ** 0.678)) * math.sin(
                math.radians(elevation))
            clear_sky = max(0.0, clear_sky)

        # Cloud as a slow random walk, so passing cloud looks like passing cloud
        # rather than white noise.
        state.cloud = clamp(state.cloud + rng.gauss(0.0, 0.02 * max(0.1, dt / 60.0)),
                            0.0, 0.95)
        attenuation = 1.0 - 0.75 * state.cloud

        state.ghi = clear_sky * attenuation
        # POA exceeds GHI on a tilted array for most of the day.
        state.poa = state.ghi * 1.08

        # Ambient follows a damped diurnal curve, lagging solar noon by ~2 h.
        peak = 24.0 + 8.0 * math.sin(math.radians(
            (state.time_of_day_s / 3600.0 - 14.0) * 15.0))
        state.ambient_temp = first_order_lag(state.ambient_temp, peak, 3600.0, dt)
        state.wind_speed = clamp(
            state.wind_speed + rng.gauss(0.0, 0.15), 0.2, 14.0)

        # Module temperature from Faiman, then lagged: a module is a real thermal
        # mass and does not track irradiance instantly. Without the lag, a passing
        # cloud would produce an unphysical temperature step.
        target = D.module_temp_faiman(state.ambient_temp, state.poa,
                                      state.wind_speed)
        state.module_temp = first_order_lag(state.module_temp, target, 420.0, dt)
        state.cell_temp = D.cell_temp_from_module(state.module_temp, state.poa)

    def apply_measurements(self, state: SolarState, *, poa: float | None = None,
                           ghi: float | None = None,
                           module_temp: float | None = None,
                           ambient_temp: float | None = None,
                           wind_speed: float | None = None) -> None:
        """Drive the model from REAL sensors (§2.2). This is what turns the
        simulator into a twin.

        Cell temperature prefers the measured back-of-module RTD and falls back to
        Faiman from ambient + wind — which is the reason §2.2 asks for ambient and
        wind at all. A zone whose RTD has failed keeps a usable baseline instead of
        going blind, and `sensor_fault` in FAULTS exercises exactly that path.
        """
        state.measured_env = True
        if poa is not None:
            state.poa = max(0.0, float(poa))
        if ghi is not None:
            state.ghi = max(0.0, float(ghi))
        if ambient_temp is not None:
            state.ambient_temp = float(ambient_temp)
        if wind_speed is not None:
            state.wind_speed = max(0.0, float(wind_speed))
        if module_temp is not None:
            state.module_temp = float(module_temp)
        else:
            state.module_temp = D.module_temp_faiman(
                state.ambient_temp, state.poa, state.wind_speed)
        state.cell_temp = D.cell_temp_from_module(state.module_temp, state.poa)

    # ── Per-string electrical model ─────────────────────────────────────────

    def string_operating_point(self, state: SolarState, s: StringState) -> dict:
        """One string's I-V operating point under current conditions.

        Each degradation acts on the parameter its physical mechanism actually
        affects — see the module docstring. That is what makes the residuals
        diagnostic rather than merely indicative.
        """
        if s.open_circuit or state.poa <= 1.0:
            return {"v_mp": 0.0, "i_mp": 0.0, "p_mp": 0.0, "v_oc": 0.0,
                    "i_sc": 0.0, "fill_factor": 0.0, "r_sh_ref": None}

        params = self.module.scale_to_string(s.modules_in_series)
        # Shading/bypass removes modules from the SERIES chain: voltage falls,
        # current does not. Rebuilding the string at a reduced module count is the
        # faithful representation of a bypass diode conducting.
        active = max(1, int(round(s.modules_in_series * (1.0 - s.bypassed_fraction))))
        if active != s.modules_in_series:
            params = self.module.scale_to_string(active)

        from dataclasses import replace
        params = replace(params,
                         r_s=params.r_s * s.rs_factor,
                         r_sh_ref=params.r_sh_ref * s.rsh_factor)

        # Soiling attenuates the light reaching the cells, so it enters as an
        # effective irradiance rather than as a fudge on the output.
        effective_poa = state.poa * clamp(s.soiling, 0.0, 1.0)
        operating = D.translate(params, effective_poa, state.cell_temp)
        mpp = D.max_power_point(operating)

        return {
            "v_mp": mpp.v_mp, "i_mp": mpp.i_mp, "p_mp": mpp.p_mp,
            "v_oc": mpp.v_oc, "i_sc": mpp.i_sc,
            "fill_factor": mpp.fill_factor,
            "r_sh_ref": D.estimate_rsh_ref(mpp.v_mp, mpp.i_mp, operating),
            "effective_poa": effective_poa,
        }

    def ideal_operating_point(self, state: SolarState) -> dict:
        """The CLEAN-array baseline: same weather, no degradation.

        This is `P_Modelled` in §4.2, and the reason it is computed with the same
        solver on the same weather is that any difference then has to come from the
        array itself. Comparing measured output against a datasheet number scaled by
        irradiance — the common shortcut — folds temperature, spectrum and inverter
        efficiency into the "fault", and every hot afternoon becomes an alert.
        """
        if state.poa <= 1.0:
            return {"p_dc": 0.0, "v_mp": 0.0, "i_mp": 0.0, "p_ac": 0.0,
                    "v_oc": 0.0, "i_sc": 0.0, "fill_factor": 0.0}
        params = self.module.scale_to_string(state.modules_in_series)
        operating = D.translate(params, state.poa, state.cell_temp)
        mpp = D.max_power_point(operating)
        active = sum(1 for s in state.strings if not s.open_circuit)
        p_dc = mpp.p_mp * max(0, active)
        efficiency = inverter_efficiency(p_dc / max(1.0, state.inverter_rating_w))
        return {
            "p_dc": p_dc, "v_mp": mpp.v_mp, "i_mp": mpp.i_mp * max(0, active),
            "p_ac": min(p_dc * efficiency, state.inverter_rating_w),
            "v_oc": mpp.v_oc, "i_sc": mpp.i_sc, "fill_factor": mpp.fill_factor,
        }

    # ── Forward ─────────────────────────────────────────────────────────────

    def forward(self, state: SolarState, dt: float = 1.0) -> dict:
        """Advance the array one step and return a signal frame."""
        self._advance_environment(state, dt)
        self._advance_degradation(state, dt)

        rng = state.rng()
        per_string = [(s, self.string_operating_point(state, s))
                      for s in state.strings]

        # Strings in parallel into one MPP tracker: currents add, and the array sits
        # at a single voltage. Mismatched strings therefore cannot each sit at their
        # own MPP — the classic mismatch loss, and the reason a single shaded string
        # costs more than its own share of the output.
        live = [(s, op) for s, op in per_string if op["p_mp"] > 0]
        if live:
            v_array = sum(op["v_mp"] for _, op in live) / len(live)
            i_total = 0.0
            for _s, op in live:
                # Off-MPP penalty, quadratic in the voltage mismatch: a string forced
                # away from its own maximum-power voltage loses output.
                mismatch = abs(op["v_mp"] - v_array) / max(1.0, op["v_mp"])
                i_total += op["i_mp"] * max(0.0, 1.0 - 1.6 * mismatch ** 2)
            p_dc = v_array * i_total
        else:
            v_array, i_total, p_dc = 0.0, 0.0, 0.0

        # Inverter: efficiency curve, thermal derate, clipping at the rating.
        load_fraction = p_dc / max(1.0, state.inverter_rating_w)
        efficiency = inverter_efficiency(load_fraction) * state.derate_factor
        p_ac = min(p_dc * efficiency, state.inverter_rating_w * state.derate_factor)

        # Heat-sink temperature follows losses, lagged by its thermal mass.
        losses = max(0.0, p_dc - p_ac)
        target_heatsink = state.ambient_temp + 28.0 * (
            losses / max(1.0, state.inverter_rating_w * 0.04))
        state.heatsink_temp = first_order_lag(state.heatsink_temp,
                                              target_heatsink, 240.0, dt)

        state.operating_state = self._operating_state(state, p_dc)

        ideal = self.ideal_operating_point(state)
        delta_p = p_dc - ideal["p_dc"]
        delta_p_pct = (100.0 * delta_p / ideal["p_dc"]) if ideal["p_dc"] > 1.0 else 0.0

        nameplate = self.nameplate_w(state)
        # Performance ratio: measured yield over the yield the nameplate would give
        # at the measured irradiance. The industry's headline KPI (IEC 61724).
        pr = 0.0
        if state.poa > 20.0 and nameplate > 0:
            pr = p_ac / (nameplate * state.poa / D.G_REF)

        currents = [op["i_mp"] for _, op in per_string]
        imbalance = 0.0
        if currents and max(currents) > 0.5:
            imbalance = 100.0 * (max(currents) - min(currents)) / max(currents)

        rsh_values = [op["r_sh_ref"] for _, op in per_string
                      if op["r_sh_ref"] is not None]
        frame = {
            SIGNALS["poa"]: round(jitter(rng, state.poa, 0.004), 2),
            SIGNALS["ghi"]: round(state.ghi, 2),
            SIGNALS["ambient_temp"]: round(jitter(rng, state.ambient_temp, 0.003), 2),
            SIGNALS["wind"]: round(state.wind_speed, 2),
            SIGNALS["module_temp"]: round(jitter(rng, state.module_temp, 0.003), 2),
            SIGNALS["cell_temp"]: round(state.cell_temp, 2),
            SIGNALS["dc_voltage"]: round(jitter(rng, v_array, 0.002), 2),
            SIGNALS["dc_current"]: round(jitter(rng, i_total, 0.004), 3),
            SIGNALS["dc_power"]: round(p_dc, 1),
            SIGNALS["ac_power"]: round(p_ac, 1),
            SIGNALS["efficiency"]: round(100.0 * efficiency, 2),
            SIGNALS["heatsink_temp"]: round(state.heatsink_temp, 2),
            SIGNALS["frequency"]: round(50.0 + rng.gauss(0.0, 0.012), 3),
            SIGNALS["state"]: float(state.operating_state),
            SIGNALS["modelled_dc"]: round(ideal["p_dc"], 1),
            SIGNALS["delta_p"]: round(delta_p, 1),
            SIGNALS["delta_p_pct"]: round(delta_p_pct, 2),
            SIGNALS["pr"]: round(pr, 4),
            SIGNALS["ff"]: round(
                sum(op["fill_factor"] for _, op in live) / len(live), 4)
            if live else 0.0,
            SIGNALS["imbalance"]: round(imbalance, 2),
            SIGNALS["facility_load"]: round(
                jitter(rng, state.facility_load_kw, 0.02), 2),
            SIGNALS["net_grid"]: round(state.facility_load_kw - p_ac / 1000.0, 2),
        }
        if rsh_values:
            frame[SIGNALS["rsh"]] = round(sum(rsh_values) / len(rsh_values), 1)

        # Per-string signals, keyed by string id, so the §4.2 rules and the §5.1
        # heat map can see across strings rather than only at the aggregate.
        frame["_strings"] = [
            {"string_id": s.string_id,
             "current": round(op["i_mp"], 3),
             "voltage": round(op["v_mp"], 2),
             "power": round(op["p_mp"], 1),
             "v_oc": round(op["v_oc"], 2),
             "fill_factor": round(op["fill_factor"], 4),
             "r_sh_ref": (round(op["r_sh_ref"], 1)
                          if op["r_sh_ref"] is not None else None),
             "soiling": round(s.soiling, 4),
             "bypassed": round(s.bypassed_fraction, 4),
             "open": s.open_circuit}
            for s, op in per_string
        ]
        return frame

    def _operating_state(self, state: SolarState, p_dc: float) -> int:
        """Which of §2.1's operating states the inverter is in.

        Reported because the §4.2 residual rules must not fire when zero output is
        EXPECTED — a soiling ticket raised at 5 a.m. because the inverter was asleep
        costs a truck roll and teaches operators to ignore the alerts.
        """
        if state.fault == "inverter_derate" and state.fault_severity > 0.8:
            return 6                        # fault
        if state.poa < 15.0:
            return 1                        # sleeping
        if p_dc < state.inverter_rating_w * 0.005:
            return 2                        # starting
        if state.derate_factor < 0.98:
            return 4                        # throttled / derated
        return 3                            # mppt

    # ── Degradation ─────────────────────────────────────────────────────────

    def _advance_degradation(self, state: SolarState, dt: float) -> None:
        """Advance wear and any injected fault.

        Rates are per-DAY and scaled by dt, so a projection that runs at 60× wall
        clock ages the array 60× as fast — which is what makes `predict.py`'s RUL
        consistent with the live twin rather than a separately-tuned demo ramp.
        """
        days = dt / 86400.0
        severity = clamp(state.fault_severity, 0.0, 1.0)
        state.rng()

        for s in state.strings:
            # Natural soiling accumulates while dry and saturates — dust reaches an
            # equilibrium rather than growing without bound.
            if state.poa > 5.0:
                s.days_since_clean += days
                equilibrium = 0.93
                s.soiling = max(equilibrium,
                                s.soiling - 0.0012 * days * (s.soiling - equilibrium + 0.15))
            # Baseline PID/ageing: roughly 0.5 %/year of shunt resistance.
            s.rsh_factor = max(0.15, s.rsh_factor - 0.000014 * days)

        if state.fault == "none" or severity <= 0:
            return

        if state.fault == "soiling":
            # UNIFORM across the zone — the §4.2 discriminator. Applying it to every
            # string equally is what makes the resulting signature symmetric.
            for s in state.strings:
                s.soiling = max(0.55, s.soiling - 0.06 * severity * days * 30.0)

        elif state.fault == "shading":
            # ONE string, and it removes VOLTAGE. Bypass diodes conduct around the
            # shaded substring, so the string loses modules from its series chain
            # while its current stays close to normal.
            if state.strings:
                target = state.strings[len(state.strings) // 3]
                target.bypassed_fraction = clamp(0.34 * severity, 0.0, 0.66)

        elif state.fault in ("pid", "delamination"):
            # Secular shunt decay. Deliberately SLOW — §4.2 asks for a 90-day trend,
            # and a fault that manifested in minutes would not exercise the rule that
            # has to distinguish degradation from weather.
            for s in state.strings:
                s.rsh_factor = max(0.08, s.rsh_factor - 0.011 * severity * days)
                if state.fault == "delamination":
                    s.soiling = max(0.70, s.soiling - 0.004 * severity * days)

        elif state.fault == "resistive":
            for s in state.strings:
                s.rs_factor = min(6.0, s.rs_factor + 0.05 * severity * days)

        elif state.fault == "string_open":
            if state.strings:
                state.strings[-1].open_circuit = severity > 0.4

        elif state.fault == "inverter_derate":
            state.derate_factor = clamp(1.0 - 0.45 * severity, 0.3, 1.0)

        elif state.fault == "sensor_fault":
            # The pyranometer reads high, so the modelled baseline is inflated and
            # ΔP goes negative with nothing actually wrong on the DC side. This is
            # the false-positive case every residual rule must survive, which is why
            # it is a first-class injectable fault rather than an afterthought.
            state.poa *= (1.0 + 0.25 * severity)

    def inject(self, state: SolarState, fault: str, severity: float = 0.6) -> None:
        """Apply a named fault. Unknown names raise rather than being ignored — a
        silently-ignored injection makes a training scenario (§5.3) look like the
        twin failed to detect a fault that was never actually applied."""
        if fault not in FAULTS:
            raise ValueError(
                f"unknown fault '{fault}'. Known: {sorted(FAULTS)}")
        state.fault = fault
        state.fault_severity = clamp(severity, 0.0, 1.0)
        if fault == "none":
            state.fault_severity = 0.0
            state.derate_factor = 1.0
            for s in state.strings:
                s.bypassed_fraction = 0.0
                s.open_circuit = False

    def clean_array(self, state: SolarState) -> None:
        """Wash the array — what the §4.2 cleaning ticket results in. Present so a
        closed work order can be reflected in the twin, which is how the platform
        can later show whether the intervention actually recovered the output."""
        for s in state.strings:
            s.soiling = 0.995
            s.days_since_clean = 0.0
        if state.fault == "soiling":
            state.fault, state.fault_severity = "none", 0.0

    # ── Tier-A residuals ────────────────────────────────────────────────────

    def residuals(self, frame: dict) -> dict:
        """Physics residuals — the platform's Tier-A monitoring input.

        ΔP is the headline (§4.2). The others exist because ΔP alone cannot separate
        the root causes: current imbalance separates uniform soiling from localised
        shading, and fill-factor loss separates resistive from shunt degradation.
        """
        out = {}
        measured = frame.get(SIGNALS["dc_power"], 0.0)
        modelled = frame.get(SIGNALS["modelled_dc"], 0.0)
        if modelled > 1.0:
            out["delta_p_w"] = round(measured - modelled, 1)
            out["delta_p_pct"] = round(100.0 * (measured - modelled) / modelled, 2)
        out["current_imbalance_pct"] = frame.get(SIGNALS["imbalance"], 0.0)
        ff = frame.get(SIGNALS["ff"], 0.0)
        if ff > 0:
            out["fill_factor_deficit"] = round(0.79 - ff, 4)
        return out

    # ── Health ──────────────────────────────────────────────────────────────

    def health_index(self, frame: dict) -> float:
        """0..1 overall condition.

        At night there is nothing to judge, so health is reported as 1.0 rather than
        0.0. A twin that shows every array as critical every night trains its
        operators to ignore the health ring entirely — and the array is genuinely
        fine, it is simply dark.
        """
        if frame.get(SIGNALS["poa"], 0.0) < 20.0:
            return 1.0

        margins = []
        delta = frame.get(SIGNALS["delta_p_pct"])
        if delta is not None:
            # 0 % deficit is perfect; -25 % is critical. §4.2's soiling trigger at
            # -12 % therefore lands mid-scale, which is the intent: a cleaning
            # ticket is a warning, not an emergency.
            margins.append(clamp((delta + 25.0) / 25.0))
        pr = frame.get(SIGNALS["pr"])
        if pr:
            margins.append(margin_lo(pr, 0.82, redlines.performance_ratio_min))
        margins.append(margin_hi(frame.get(SIGNALS["imbalance"], 0.0), 2.0,
                                 redlines.current_imbalance_pct))
        margins.append(margin_hi(frame.get(SIGNALS["heatsink_temp"], 25.0), 55.0,
                                 redlines.heatsink_temp))
        margins.append(margin_hi(frame.get(SIGNALS["cell_temp"], 25.0), 55.0,
                                 redlines.cell_temp))
        ff = frame.get(SIGNALS["ff"])
        if ff:
            margins.append(margin_lo(ff, 0.79, redlines.fill_factor_min))
        return round(worst_health(margins), 3)
