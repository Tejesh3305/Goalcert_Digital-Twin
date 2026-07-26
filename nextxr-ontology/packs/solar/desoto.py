"""
desoto.py — the De Soto 5-parameter single-diode PV model.

This is the mathematical core of the solar twin: given irradiance and cell
temperature it produces the I-V curve a healthy string SHOULD deliver. Every
fault signature in this pack is a residual against that curve, so an error here
propagates into every diagnosis — which is why the model is implemented from the
published equations rather than approximated, and why `test_desoto.py` pins it
against independently-known physical invariants.

THE EQUATION
------------
The single-diode (five-parameter) equivalent circuit:

    I = I_L - I_0·[exp((V + I·R_s)/a) - 1] - (V + I·R_s)/R_sh

    I_L   light-induced photocurrent            [A]
    I_0   diode reverse saturation current       [A]
    a     modified ideality factor = n·N_s·k·T/q [V]  (NOTE: volts, not unitless)
    R_s   series resistance                      [Ω]
    R_sh  shunt resistance                       [Ω]

It is IMPLICIT in I — I appears on both sides — so it cannot be evaluated
directly. Two standard routes exist:

  * Lambert W closed form (Jain & Kapoor 2004). Exact, but its argument is
    exp(R_sh·(R_s·I_L + R_s·I_0 + V)/(a·(R_s+R_sh))), which overflows a float64
    for entirely ordinary module parameters. Using it safely means scaled/asymptotic
    branches — real work, and `scipy.special.lambertw` is not a dependency here.

  * Newton-Raphson on the residual. Converges in 3-6 iterations from I = I_L
    because the function is smooth and monotonic in I over the operating range.

Newton is used, with a bisection fallback for the rare case where a pathological
parameter set (R_sh → 0 during a hard shunt fault) makes the derivative
unhelpful. Never silently returns an unconverged value: `solve_current` reports
convergence, and the callers that cannot tolerate a bad answer check it.

DE SOTO TRANSLATION (reference conditions → operating conditions)
-----------------------------------------------------------------
De Soto et al., "Improvement and validation of a model for photovoltaic array
performance", Solar Energy 80 (2006) 78-88. Reference is STC: 1000 W/m², 25 °C.

    a    = a_ref · (T_c / T_ref)
    I_L  = (G / G_ref) · (I_L_ref + α_Isc · (T_c - T_ref))
    I_0  = I_0_ref · (T_c / T_ref)³ · exp[ (1/k) · (E_g_ref/T_ref - E_g/T_c) ]
    E_g  = E_g_ref · (1 - 0.0002677 · (T_c - T_ref))          [silicon]
    R_sh = R_sh_ref · (G_ref / G)
    R_s  = R_s_ref                                            [constant]

Temperatures are KELVIN in every one of those. Mixing °C into the a or I_0
expressions is the classic error and it produces a curve that looks plausible at
25 °C and is badly wrong everywhere else — so conversion happens once, at the
boundary, and the internal helpers only ever see Kelvin.

WHAT R_sh MEANS FOR THIS TWIN
-----------------------------
R_sh rising as irradiance falls is the model's own behaviour. The spec's PID /
delamination signature (§4.2) is a *secular* decline in R_sh_ref, fitted over 90
days after normalising out irradiance. `estimate_rsh_ref` is that normalisation,
and it is why the shunt-decay rule can distinguish real degradation from a cloudy
week.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

# Physical constants.
BOLTZMANN_EV = 8.617332478e-5     # eV/K — k in electron-volts, matching E_g in eV
KELVIN_0C = 273.15

# Reference (Standard Test Conditions).
G_REF = 1000.0                    # W/m²
T_REF_C = 25.0                    # °C
T_REF_K = T_REF_C + KELVIN_0C     # 298.15 K
EG_REF_SI = 1.121                 # eV — silicon band gap at 25 °C
DEG_DEG_DT_SI = -0.0002677        # 1/K — dE_g/dT relative coefficient (De Soto)

# exp() overflow guard. math.exp overflows above ~709.78; 700 keeps a margin and
# the current at that point is astronomically past any physical operating region,
# so clamping cannot distort a real answer — it only prevents an OverflowError
# from aborting a solve during an intermediate Newton step.
_EXP_MAX = 700.0


def _safe_exp(x: float) -> float:
    return math.exp(x) if x < _EXP_MAX else math.exp(_EXP_MAX)


@dataclass(frozen=True)
class ModuleParams:
    """Five-parameter set plus the coefficients needed to translate it.

    `i_l_ref`, `i_0_ref`, `r_s`, `r_sh_ref`, `a_ref` are the five. They are
    per-STRING when used for string-level modelling: a series string of N modules
    has a_string = N·a_module and R_s,string = N·R_s,module, while currents are
    unchanged. `from_module` does that scaling so a caller cannot get it subtly
    wrong by hand.
    """
    i_l_ref: float                # A     light current at STC
    i_0_ref: float                # A     saturation current at STC
    r_s: float                    # Ω     series resistance
    r_sh_ref: float               # Ω     shunt resistance at STC
    a_ref: float                  # V     modified ideality factor at STC
    alpha_isc: float = 0.0        # A/K   temperature coefficient of I_sc
    eg_ref: float = EG_REF_SI     # eV    band gap at T_ref
    cells_in_series: int = 1      # informational; scaling is explicit below

    def scale_to_string(self, modules_in_series: int) -> "ModuleParams":
        """Series-connect `modules_in_series` identical modules.

        In series the same current flows through every module while voltages add,
        so the voltage-dimensioned parameters (a, R_s, R_sh) scale by N and the
        current-dimensioned ones (I_L, I_0) do not. Getting this backwards is a
        common and quiet error: it yields a curve of the right shape at entirely
        the wrong voltage.
        """
        n = max(1, int(modules_in_series))
        return replace(
            self,
            r_s=self.r_s * n,
            r_sh_ref=self.r_sh_ref * n,
            a_ref=self.a_ref * n,
            cells_in_series=self.cells_in_series * n,
        )


@dataclass(frozen=True)
class OperatingParams:
    """The five parameters translated to one (irradiance, cell temperature)."""
    i_l: float
    i_0: float
    r_s: float
    r_sh: float
    a: float
    irradiance: float
    t_cell_c: float


def translate(params: ModuleParams, irradiance: float, t_cell_c: float,
              *, airmass_modifier: float = 1.0) -> OperatingParams:
    """De Soto translation from STC to operating conditions.

    `airmass_modifier` is De Soto's M/M_ref spectral term. It defaults to 1.0
    because computing it needs solar position and a spectral model; when a site
    supplies one it multiplies the photocurrent, which is exactly where De Soto
    places it.

    Irradiance is floored rather than allowed to reach zero: R_sh ∝ G_ref/G
    diverges at G=0, and a division by zero in the middle of a night-time tick is
    not a useful failure. At 0.1 W/m² the modelled output is already nil.
    """
    g = max(0.1, float(irradiance))
    t_cell_k = float(t_cell_c) + KELVIN_0C
    if t_cell_k <= 0:
        raise ValueError(f"cell temperature {t_cell_c} °C is below absolute zero")

    # a scales linearly with absolute temperature (a = n·Ns·k·T/q).
    a = params.a_ref * (t_cell_k / T_REF_K)

    # Photocurrent: linear in irradiance, with the I_sc temperature coefficient.
    i_l = (g / G_REF) * airmass_modifier * (
        params.i_l_ref + params.alpha_isc * (t_cell_k - T_REF_K))
    i_l = max(0.0, i_l)

    # Saturation current: the strongly temperature-dependent term. The band gap
    # itself shrinks with temperature, which is the second-order effect De Soto
    # adds over the simpler exponential models.
    eg = params.eg_ref * (1.0 + DEG_DEG_DT_SI * (t_cell_k - T_REF_K))
    exponent = (1.0 / BOLTZMANN_EV) * (params.eg_ref / T_REF_K - eg / t_cell_k)
    i_0 = params.i_0_ref * (t_cell_k / T_REF_K) ** 3 * _safe_exp(exponent)

    # Shunt resistance is inversely proportional to irradiance.
    r_sh = params.r_sh_ref * (G_REF / g)

    return OperatingParams(i_l=i_l, i_0=i_0, r_s=params.r_s, r_sh=r_sh, a=a,
                           irradiance=g, t_cell_c=float(t_cell_c))


# ── Solving the implicit equation ───────────────────────────────────────────


def _residual(i: float, v: float, p: OperatingParams) -> float:
    """f(I) = 0 at the solution."""
    vd = v + i * p.r_s                       # voltage across the diode/shunt node
    return (p.i_l
            - p.i_0 * (_safe_exp(vd / p.a) - 1.0)
            - vd / p.r_sh
            - i)


def _residual_prime(i: float, v: float, p: OperatingParams) -> float:
    vd = v + i * p.r_s
    return -p.i_0 * (p.r_s / p.a) * _safe_exp(vd / p.a) - p.r_s / p.r_sh - 1.0


def solve_current(v: float, p: OperatingParams, *,
                  tol: float = 1e-10, max_iter: int = 60) -> tuple[float, bool]:
    """Current at terminal voltage `v`. Returns (current, converged).

    Newton from I = I_L, which is above the true solution for every V ≥ 0 and on
    the smooth side of the exponential, so the iteration walks down to the root
    without overshooting into the region where exp() dominates.

    `converged` is returned rather than raising because a single non-converged
    point inside a 200-point I-V sweep should not discard the sweep — but a caller
    computing a maintenance threshold from one operating point must be able to
    refuse to act on a bad number. Both needs are real, so the caller decides.
    """
    i = p.i_l
    for _ in range(max_iter):
        f = _residual(i, v, p)
        if abs(f) < tol:
            return i, True
        fp = _residual_prime(i, v, p)
        if fp == 0.0 or not math.isfinite(fp):
            break
        step = f / fp
        i_next = i - step
        if not math.isfinite(i_next):
            break
        # Damp a wild step. Undamped Newton on this function can jump far negative
        # when R_sh is tiny (a hard shunt fault), from where exp() saturates and
        # the derivative carries no information.
        if abs(i_next - i) > max(1.0, abs(p.i_l)):
            i_next = i + math.copysign(max(1.0, abs(p.i_l)) * 0.5, i_next - i)
        i = i_next
    else:
        return i, abs(_residual(i, v, p)) < 1e-6

    # Bisection fallback. f is monotonically decreasing in I, so a sign change is
    # bracketed by [-|I_L|-1, I_L+1] for any physically meaningful parameter set.
    lo, hi = -abs(p.i_l) - 1.0, p.i_l + 1.0
    f_lo, f_hi = _residual(lo, v, p), _residual(hi, v, p)
    if f_lo * f_hi > 0:
        return i, False
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = _residual(mid, v, p)
        if abs(f_mid) < tol:
            return mid, True
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi), True


def short_circuit_current(p: OperatingParams) -> float:
    """I_sc — current at V = 0. Slightly below I_L because the shunt path and the
    series drop both take a share even at short circuit."""
    i, _ = solve_current(0.0, p)
    return i


def open_circuit_voltage(p: OperatingParams, *,
                         tol: float = 1e-10, max_iter: int = 100) -> float:
    """V_oc — the voltage where I = 0.

    At I = 0 the implicit term vanishes (V_d = V), so this reduces to a
    well-behaved scalar root-find with an analytic derivative. Bisection between
    0 and a generous upper bound is used rather than Newton: the exponential makes
    Newton's first step from a poor guess enormous, and V_oc is computed once per
    frame so the extra iterations cost nothing measurable.
    """
    if p.i_l <= 0:
        return 0.0

    def f(v: float) -> float:
        return p.i_l - p.i_0 * (_safe_exp(v / p.a) - 1.0) - v / p.r_sh

    lo = 0.0
    # Analytic upper bound, ignoring the shunt: V where the diode alone carries
    # I_L. The real V_oc is below it, because the shunt siphons some current.
    hi = p.a * math.log(max(1.0, p.i_l / max(p.i_0, 1e-30)) + 1.0)
    hi = max(hi, 1e-3) * 1.5
    if f(hi) > 0:
        for _ in range(60):                  # widen if the bound was optimistic
            hi *= 1.5
            if f(hi) <= 0:
                break
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if abs(fm) < tol:
            return mid
        if fm > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class MaxPowerPoint:
    v_mp: float
    i_mp: float
    p_mp: float
    v_oc: float
    i_sc: float

    @property
    def fill_factor(self) -> float:
        """FF = P_mp / (V_oc·I_sc). A healthy crystalline module is 0.7-0.8;
        falling FF with V_oc and I_sc intact is the resistive-loss signature."""
        denom = self.v_oc * self.i_sc
        return (self.p_mp / denom) if denom > 0 else 0.0


def max_power_point(p: OperatingParams, *, iterations: int = 80) -> MaxPowerPoint:
    """Find the MPP by ternary search on V over [0, V_oc].

    P(V) is unimodal on that interval for a single-diode model, which is exactly
    the condition ternary search needs, and unlike a derivative root-find it
    cannot be thrown off by the exponential's stiffness near V_oc. 80 iterations
    narrows the bracket by (2/3)^80 — far past double precision — and each step is
    two cheap Newton solves.

    This is what an inverter's MPP tracker is trying to find, so the modelled
    P_mp is directly comparable to measured DC power. That comparison IS the
    residual ΔP the whole maintenance layer is built on.
    """
    v_oc = open_circuit_voltage(p)
    i_sc = short_circuit_current(p)
    if v_oc <= 0:
        return MaxPowerPoint(0.0, 0.0, 0.0, 0.0, max(0.0, i_sc))

    lo, hi = 0.0, v_oc

    def power(v: float) -> float:
        i, _ = solve_current(v, p)
        return v * max(0.0, i)

    for _ in range(iterations):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if power(m1) < power(m2):
            lo = m1
        else:
            hi = m2

    v_mp = 0.5 * (lo + hi)
    i_mp, _ = solve_current(v_mp, p)
    i_mp = max(0.0, i_mp)
    return MaxPowerPoint(v_mp=v_mp, i_mp=i_mp, p_mp=v_mp * i_mp,
                         v_oc=v_oc, i_sc=max(0.0, i_sc))


def iv_curve(p: OperatingParams, points: int = 60) -> list[dict]:
    """Sampled I-V and P-V curve, for the Operations Command Center chart (§5.1).

    Sampled to V_oc inclusive so the curve visibly lands on the axis; a curve that
    stops short reads as a measurement artefact to anyone who knows what an I-V
    trace should look like.
    """
    v_oc = open_circuit_voltage(p)
    if v_oc <= 0:
        return []
    out = []
    n = max(2, int(points))
    for k in range(n + 1):
        v = v_oc * k / n
        i, ok = solve_current(v, p)
        i = max(0.0, i)
        out.append({"v": round(v, 4), "i": round(i, 5),
                    "p": round(v * i, 4), "converged": ok})
    return out


# ── Diagnostics support ─────────────────────────────────────────────────────


def estimate_rsh_ref(v: float, i: float, p: OperatingParams) -> Optional[float]:
    """Back out R_sh_ref from ONE measured operating point (V, I).

    Rearranging the diode equation for the shunt term:

        I_shunt = I_L - I_0·[exp(V_d/a) - 1] - I        with V_d = V + I·R_s
        R_sh    = V_d / I_shunt
        R_sh_ref = R_sh · (G / G_ref)

    The last step is the important one: it removes the model's own 1/G dependence,
    so the result is comparable across days with different weather. Without that
    normalisation a cloudy week looks exactly like the PID degradation the
    90-day shunt-decay rule is watching for (§4.2), and the rule would raise a
    ticket every autumn.

    Returns None when the point carries no shunt information — near open circuit
    the diode dominates and the inferred value is numerically meaningless, so a
    number here would be worse than an abstention.
    """
    if p.irradiance < 200.0:
        return None                     # too dark for a trustworthy inference
    v_d = v + i * p.r_s
    if v_d <= 0:
        return None
    i_diode = p.i_0 * (_safe_exp(v_d / p.a) - 1.0)
    i_shunt = p.i_l - i_diode - i
    # Below ~1 mA the quotient is dominated by measurement noise and by error in
    # I_0; refuse rather than emit a wild R_sh that would trip the trend rule.
    if i_shunt < 1e-3:
        return None
    r_sh = v_d / i_shunt
    if not math.isfinite(r_sh) or r_sh <= 0:
        return None
    return r_sh * (p.irradiance / G_REF)


# ── Cell temperature models ─────────────────────────────────────────────────
#
# The spec bonds PT100/RTD sensors to the back of reference modules, so module
# temperature is MEASURED. Two conversions are still needed, and both are here so
# no caller improvises one.

# SAPM back-of-module → cell offset for a glass/cell/polymer module in an open
# rack. The cell sits a few degrees above its own back sheet under full sun,
# scaling with irradiance because that is what drives the gradient.
SAPM_DELTA_T = 3.0


def cell_temp_from_module(t_module_c: float, irradiance: float,
                          delta_t: float = SAPM_DELTA_T) -> float:
    """Cell temperature from a back-of-module RTD (the spec's §2.2 sensor)."""
    return t_module_c + delta_t * (max(0.0, irradiance) / G_REF)


# Faiman coefficients for a free-standing array: U0 conducts, U1 scales with wind.
FAIMAN_U0 = 25.0                  # W/(m²·K)
FAIMAN_U1 = 6.84                  # W·s/(m³·K)


def module_temp_faiman(t_ambient_c: float, irradiance: float,
                       wind_speed: float = 1.0,
                       u0: float = FAIMAN_U0, u1: float = FAIMAN_U1) -> float:
    """Module temperature from ambient, irradiance and wind — the FALLBACK when a
    zone's RTD is missing or has failed.

    This is why §2.2 asks for ambient temperature and wind speed at all: they let
    the twin keep modelling a thermal zone whose reference RTD has died, rather
    than going blind. `wind_speed` is floored because a zero-wind divisor would
    make the estimate diverge, and dead-calm conditions do not actually stop
    convective cooling.
    """
    ws = max(0.2, float(wind_speed))
    return float(t_ambient_c) + max(0.0, irradiance) / (u0 + u1 * ws)


# ── A reference module ──────────────────────────────────────────────────────
#
# A 550 W mono-PERC panel of the class used in commercial rooftop arrays.
# Datasheet at STC: P_mp 550 W, V_mp 41.8 V, I_mp 13.16 A, V_oc 49.9 V,
# I_sc 13.95 A, 144 half-cells (72 electrically in series), α_Isc +0.045 %/K.
#
# The five parameters are NOT on any datasheet — extracting them is the entire
# reason a fitting step exists. These were fitted numerically against the five
# datasheet points above, and the fit is asserted by `test_desoto.py`, so an edit
# that breaks it fails loudly instead of quietly shifting every baseline the
# maintenance layer compares residuals against.
#
# Fit quality: I_sc, V_oc and P_mp reproduce exactly; V_mp/I_mp land within 0.6 %
# (their PRODUCT is exact — the modelled MPP sits a hair to the left of the
# datasheet's, well inside the ±3 % binning tolerance a real module ships with).
#
# a_ref is physically principled rather than a free knob: a = n·N_s·kT/q, and
# 72 × 0.025693 V = 1.84987 V is ideality factor n = 1.000 exactly. A fit that had
# driven n far from unity would have signalled the parameter set was compensating
# for an error elsewhere in the translation, so it is worth stating that it did not.
REFERENCE_MODULE = ModuleParams(
    i_l_ref=13.95576,
    i_0_ref=2.669577e-11,
    r_s=0.19818,
    r_sh_ref=480.0,
    a_ref=1.84987,                 # = 72 cells × kT/q(298.15 K), i.e. n = 1.0
    alpha_isc=13.95 * 0.00045,     # A/K, from +0.045 %/K of I_sc
    eg_ref=EG_REF_SI,
    cells_in_series=72,
)

# Datasheet nameplate of the module above, kept beside it so ratings used for
# performance ratio come from one place rather than being retyped per call site.
REFERENCE_MODULE_PMP_STC = 550.0   # W

# Verified emergent behaviour of the fitted set, recorded because these are the
# numbers a PV engineer will sanity-check first and they are NOT inputs — they
# fall out of the De Soto translation, which is what makes them evidence the
# translation is right:
#
#     dV_oc/dT  = -0.287 %/K      (crystalline silicon: -0.27 … -0.30)
#     dP_mp/dT  = -0.348 %/K      (crystalline silicon: -0.34 … -0.40)
#     fill factor = 0.790         (healthy mono-PERC: 0.77 … 0.81)
#     I_sc ∝ G to within 0.04 % over 200-1000 W/m²

