"""
test_desoto.py — pins the De Soto five-parameter model.

WHY THIS FILE IS THE MOST IMPORTANT TEST IN THE SOLAR PACK
---------------------------------------------------------
Every §4.2 diagnosis is a residual against the curve this module produces. An error
here does not fail — it shifts the baseline, and every soiling ticket, every PID
trend and every performance-ratio figure is then measured against the wrong number.
Nothing in the system would report a fault; the numbers would simply be wrong.

So the assertions are of two kinds, and the second kind matters more:

  1. The fitted reference module reproduces its datasheet. Catches a regression in
     the parameter set.
  2. EMERGENT PHYSICS the fit did not target — the temperature coefficients, the
     linearity of I_sc in irradiance, the sign of every derivative. These are not
     inputs; they fall out of the translation equations, so they are evidence the
     TRANSLATION is right rather than merely that five numbers were tuned to hit
     five targets. A model can pass (1) and be physically nonsense.
"""

from __future__ import annotations

import math

import pytest
from packs.solar import desoto as D

# The datasheet the reference parameters were fitted against: a 550 W mono-PERC
# module, 144 half-cells (72 electrically in series).
DATASHEET = {"pmp": 550.0, "voc": 49.9, "isc": 13.95, "vmp": 41.8, "imp": 13.16}


@pytest.fixture(scope="module")
def stc():
    return D.translate(D.REFERENCE_MODULE, D.G_REF, D.T_REF_C)


# ── 1. Datasheet reproduction ───────────────────────────────────────────────


def test_reference_module_reproduces_its_datasheet(stc):
    mpp = D.max_power_point(stc)
    assert mpp.i_sc == pytest.approx(DATASHEET["isc"], rel=0.002)
    assert mpp.v_oc == pytest.approx(DATASHEET["voc"], rel=0.002)
    assert mpp.p_mp == pytest.approx(DATASHEET["pmp"], rel=0.002)
    # V_mp/I_mp individually sit within the ±3 % binning tolerance a real module
    # ships with; their PRODUCT is what the fit targeted and it is exact.
    assert mpp.v_mp == pytest.approx(DATASHEET["vmp"], rel=0.02)
    assert mpp.i_mp == pytest.approx(DATASHEET["imp"], rel=0.02)


def test_ideality_factor_is_physically_principled(stc):
    """a = n·N_s·kT/q. A fit that drove n far from 1 would mean the parameter set was
    compensating for an error elsewhere in the translation, so this is a check on the
    fit's honesty rather than on its accuracy."""
    kt_q = 1.380649e-23 * D.T_REF_K / 1.602176634e-19
    n = D.REFERENCE_MODULE.a_ref / (72 * kt_q)
    assert 0.95 <= n <= 1.10, f"ideality factor n={n:.3f} is implausible for silicon"


def test_fill_factor_is_in_the_healthy_range(stc):
    ff = D.max_power_point(stc).fill_factor
    assert 0.72 <= ff <= 0.84, f"fill factor {ff:.3f} is not a healthy crystalline value"


# ── 2. Emergent physics the fit did not target ──────────────────────────────


def test_isc_is_linear_in_irradiance():
    """I_sc ∝ G is the defining property of a photovoltaic device."""
    ratios = []
    for g in (100, 200, 400, 600, 800, 1000, 1200):
        params = D.translate(D.REFERENCE_MODULE, g, D.T_REF_C)
        ratios.append(D.short_circuit_current(params) / g)
    spread = (max(ratios) - min(ratios)) / min(ratios)
    assert spread < 0.01, f"I_sc/G varies by {spread:.3%} — not linear in irradiance"


def test_voc_temperature_coefficient_matches_silicon():
    """dV_oc/dT for crystalline silicon is -0.27 to -0.30 %/K. This is NOT an input:
    it emerges from the I_0 and band-gap translation, so it is the single best check
    that those equations are right."""
    v25 = D.open_circuit_voltage(D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0))
    v65 = D.open_circuit_voltage(D.translate(D.REFERENCE_MODULE, D.G_REF, 65.0))
    coefficient = (v65 - v25) / v25 / 40.0 * 100.0
    assert -0.32 <= coefficient <= -0.24, \
        f"dVoc/dT = {coefficient:.4f} %/K is outside the silicon range"


def test_pmp_temperature_coefficient_matches_silicon():
    """dP_mp/dT for crystalline silicon is -0.34 to -0.40 %/K."""
    p25 = D.max_power_point(D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0)).p_mp
    p65 = D.max_power_point(D.translate(D.REFERENCE_MODULE, D.G_REF, 65.0)).p_mp
    coefficient = (p65 - p25) / p25 / 40.0 * 100.0
    assert -0.42 <= coefficient <= -0.30, \
        f"dPmp/dT = {coefficient:.4f} %/K is outside the silicon range"


def test_isc_has_a_small_positive_temperature_coefficient():
    """I_sc rises slightly with temperature (+0.04 to +0.06 %/K) — the opposite sign
    to voltage, which is why power falls overall."""
    i25 = D.short_circuit_current(D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0))
    i65 = D.short_circuit_current(D.translate(D.REFERENCE_MODULE, D.G_REF, 65.0))
    coefficient = (i65 - i25) / i25 / 40.0 * 100.0
    assert 0.02 <= coefficient <= 0.08, f"dIsc/dT = {coefficient:.4f} %/K"


def test_power_falls_monotonically_with_temperature():
    powers = [D.max_power_point(D.translate(D.REFERENCE_MODULE, D.G_REF, t)).p_mp
              for t in (0, 15, 25, 40, 55, 70, 85)]
    assert powers == sorted(powers, reverse=True), \
        f"power is not monotonically decreasing in temperature: {powers}"


def test_power_rises_monotonically_with_irradiance():
    powers = [D.max_power_point(D.translate(D.REFERENCE_MODULE, g, 25.0)).p_mp
              for g in (100, 300, 500, 700, 900, 1100)]
    assert powers == sorted(powers), f"power is not monotonic in irradiance: {powers}"


# ── 3. The solver ───────────────────────────────────────────────────────────


def test_mpp_is_actually_the_maximum(stc):
    """Ternary search must find the true maximum. Verified by brute force — if the
    search were converging on a shoulder rather than the peak, every ΔP in the system
    would carry that error."""
    mpp = D.max_power_point(stc)
    best = 0.0
    for k in range(1, 400):
        v = mpp.v_oc * k / 400.0
        i, _ = D.solve_current(v, stc)
        best = max(best, v * max(0.0, i))
    assert mpp.p_mp >= best * 0.9999, \
        f"ternary search found {mpp.p_mp:.3f} W; brute force found {best:.3f} W"


def test_solver_satisfies_the_diode_equation(stc):
    """The returned current must actually solve the implicit equation — the only
    check that the Newton iteration converged rather than merely stopped."""
    for fraction in (0.0, 0.2, 0.5, 0.8, 0.95):
        v = stc.v_oc if hasattr(stc, "v_oc") else D.open_circuit_voltage(stc) * fraction
        v = D.open_circuit_voltage(stc) * fraction
        i, converged = D.solve_current(v, stc)
        assert converged, f"solver did not converge at V={v:.2f}"
        residual = D._residual(i, v, stc)
        assert abs(residual) < 1e-6, f"residual {residual:.2e} at V={v:.2f}"


def test_voc_is_where_current_is_zero(stc):
    v_oc = D.open_circuit_voltage(stc)
    i, _ = D.solve_current(v_oc, stc)
    assert abs(i) < 1e-4, f"current at V_oc is {i:.6f} A, not zero"


def test_operating_point_ordering(stc):
    """I_mp < I_sc and V_mp < V_oc, always. A violation means the solver returned a
    point off the curve."""
    mpp = D.max_power_point(stc)
    assert 0 < mpp.i_mp < mpp.i_sc
    assert 0 < mpp.v_mp < mpp.v_oc


def test_solver_survives_a_hard_shunt_fault():
    """R_sh → very small is a real fault (a dead short across the cell). The solver
    must not diverge or raise — a crash here would take down the whole poll cycle."""
    from dataclasses import replace
    shorted = replace(D.REFERENCE_MODULE, r_sh_ref=0.5)
    params = D.translate(shorted, D.G_REF, 25.0)
    mpp = D.max_power_point(params)
    assert math.isfinite(mpp.p_mp)
    assert mpp.p_mp < D.max_power_point(
        D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0)).p_mp


def test_night_produces_no_power():
    params = D.translate(D.REFERENCE_MODULE, 0.0, 15.0)
    mpp = D.max_power_point(params)
    assert mpp.p_mp == pytest.approx(0.0, abs=0.5)


def test_translate_rejects_impossible_temperature():
    with pytest.raises(ValueError):
        D.translate(D.REFERENCE_MODULE, D.G_REF, -300.0)


# ── 4. Series scaling ───────────────────────────────────────────────────────


def test_series_string_scales_voltage_not_current():
    """In series, voltages add and current does not. Getting this backwards yields a
    curve of the right shape at entirely the wrong voltage — plausible enough to
    survive a glance, and wrong enough to invalidate every string-level residual."""
    one = D.max_power_point(D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0))
    twenty = D.max_power_point(
        D.translate(D.REFERENCE_MODULE.scale_to_string(20), D.G_REF, 25.0))
    assert twenty.v_oc == pytest.approx(one.v_oc * 20, rel=0.01)
    assert twenty.i_sc == pytest.approx(one.i_sc, rel=0.01)
    assert twenty.p_mp == pytest.approx(one.p_mp * 20, rel=0.02)


def test_series_scaling_preserves_fill_factor():
    """Fill factor is a shape property, so it must be invariant under series
    scaling. If it drifts, one of a/R_s/R_sh was scaled wrongly."""
    one = D.max_power_point(D.translate(D.REFERENCE_MODULE, D.G_REF, 25.0))
    twenty = D.max_power_point(
        D.translate(D.REFERENCE_MODULE.scale_to_string(20), D.G_REF, 25.0))
    assert twenty.fill_factor == pytest.approx(one.fill_factor, abs=0.01)


# ── 5. Fault mechanisms produce DISTINGUISHABLE signatures ──────────────────
#
# This is what makes §4.2 possible. If two mechanisms produced the same signature,
# the twin could detect a fault and never identify it.


def test_soiling_takes_current_and_leaves_voltage():
    """Soiling attenuates light, so it scales photocurrent. V_oc barely moves — which
    is §4.2 row 1's discriminator ("V_dc normal; I_dc suppressed")."""
    clean = D.translate(D.REFERENCE_MODULE, 1000.0, 45.0)
    dirty = D.translate(D.REFERENCE_MODULE, 800.0, 45.0)      # 20 % soiling
    c, d = D.max_power_point(clean), D.max_power_point(dirty)
    current_loss = 1.0 - d.i_sc / c.i_sc
    voltage_loss = 1.0 - d.v_oc / c.v_oc
    assert current_loss == pytest.approx(0.20, abs=0.01)
    assert voltage_loss < 0.03, \
        f"soiling moved V_oc by {voltage_loss:.1%} — it must take current, not voltage"


def test_shunt_decay_erodes_fill_factor_and_sags_voc():
    """PID signature: R_sh falls, fill factor erodes, V_oc sags."""
    from dataclasses import replace
    healthy = D.translate(D.REFERENCE_MODULE, 1000.0, 45.0)
    degraded = D.translate(replace(D.REFERENCE_MODULE, r_sh_ref=40.0), 1000.0, 45.0)
    h, d = D.max_power_point(healthy), D.max_power_point(degraded)
    assert d.fill_factor < h.fill_factor - 0.02
    assert d.v_oc < h.v_oc


def test_series_resistance_erodes_fill_factor_but_leaves_voc():
    """Resistive-loss signature, and the discriminator against shunt decay: FF falls
    while V_oc stays INTACT. Two mechanisms, two different observable fingerprints —
    which is the whole reason the residual identifies a root cause."""
    from dataclasses import replace
    healthy = D.translate(D.REFERENCE_MODULE, 1000.0, 45.0)
    resistive = D.translate(replace(D.REFERENCE_MODULE, r_s=1.2), 1000.0, 45.0)
    h, r = D.max_power_point(healthy), D.max_power_point(resistive)
    assert r.fill_factor < h.fill_factor - 0.02
    assert r.v_oc == pytest.approx(h.v_oc, rel=0.01), \
        "series resistance must not move V_oc — that is what separates it from PID"


# ── 6. R_sh normalisation — the §4.2 row-3 enabler ──────────────────────────


def test_rsh_estimate_is_irradiance_normalised():
    """R_sh_ref recovered from an operating point must be roughly INDEPENDENT of
    irradiance.

    This is the property that makes a 90-day PID trend possible. Raw R_sh is
    inversely proportional to G in the model itself, so an un-normalised trend would
    find a 'decline' every time the weather turned — and the rule would raise a PID
    ticket every autumn.
    """
    estimates = []
    for g in (400, 600, 800, 1000):
        params = D.translate(D.REFERENCE_MODULE, g, 40.0)
        mpp = D.max_power_point(params)
        value = D.estimate_rsh_ref(mpp.v_mp, mpp.i_mp, params)
        assert value is not None, f"no estimate at {g} W/m²"
        estimates.append(value)
    spread = (max(estimates) - min(estimates)) / min(estimates)
    assert spread < 0.35, \
        (f"normalised R_sh varies {spread:.1%} across irradiance — the "
         f"normalisation is not removing the model's own 1/G dependence: {estimates}")


def test_rsh_estimate_abstains_in_the_dark():
    """Below 200 W/m² the inference is numerically meaningless, and a number would be
    worse than an abstention because it would feed the trend rule noise."""
    params = D.translate(D.REFERENCE_MODULE, 80.0, 20.0)
    mpp = D.max_power_point(params)
    assert D.estimate_rsh_ref(mpp.v_mp, mpp.i_mp, params) is None


def test_rsh_estimate_tracks_a_real_decay():
    """A genuinely lower R_sh_ref must be recovered as lower."""
    from dataclasses import replace
    values = []
    for factor in (1.0, 0.6, 0.3):
        module = replace(D.REFERENCE_MODULE,
                         r_sh_ref=D.REFERENCE_MODULE.r_sh_ref * factor)
        params = D.translate(module, 900.0, 40.0)
        mpp = D.max_power_point(params)
        values.append(D.estimate_rsh_ref(mpp.v_mp, mpp.i_mp, params))
    assert all(v is not None for v in values)
    assert values[0] > values[1] > values[2], \
        f"estimate does not track a real decay: {values}"


# ── 7. Cell temperature models ──────────────────────────────────────────────


def test_cell_temp_exceeds_module_temp_under_sun():
    assert D.cell_temp_from_module(45.0, 1000.0) > 45.0
    # No sun, no gradient.
    assert D.cell_temp_from_module(20.0, 0.0) == pytest.approx(20.0)


def test_faiman_module_temp_rises_with_irradiance_and_falls_with_wind():
    calm = D.module_temp_faiman(25.0, 900.0, wind_speed=0.5)
    windy = D.module_temp_faiman(25.0, 900.0, wind_speed=8.0)
    assert calm > windy, "wind must cool the module"
    assert D.module_temp_faiman(25.0, 900.0, 2.0) > D.module_temp_faiman(25.0, 200.0, 2.0)
    # Never below ambient — the model has no radiative cooling term, and a module
    # below ambient in sunlight would be unphysical.
    assert D.module_temp_faiman(25.0, 0.0, 2.0) >= 25.0


def test_faiman_survives_dead_calm():
    """Zero wind must not divide by zero: dead calm does not stop convection."""
    assert math.isfinite(D.module_temp_faiman(25.0, 1000.0, wind_speed=0.0))


# ── 8. I-V curve output ─────────────────────────────────────────────────────


def test_iv_curve_spans_zero_to_voc_and_is_monotonic(stc):
    curve = D.iv_curve(stc, points=40)
    assert len(curve) == 41
    assert curve[0]["v"] == 0.0
    assert curve[-1]["v"] == pytest.approx(D.open_circuit_voltage(stc), rel=1e-6)
    assert curve[-1]["i"] == pytest.approx(0.0, abs=1e-3)
    currents = [p["i"] for p in curve]
    assert currents == sorted(currents, reverse=True), \
        "current must decrease monotonically along the I-V curve"
    assert all(p["converged"] for p in curve)


def test_iv_curve_peak_matches_the_reported_mpp(stc):
    curve = D.iv_curve(stc, points=200)
    peak = max(curve, key=lambda p: p["p"])
    assert peak["p"] == pytest.approx(D.max_power_point(stc).p_mp, rel=0.005)
