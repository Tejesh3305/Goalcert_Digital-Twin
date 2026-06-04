"""
hvac.py — generative HVAC plant models.

Coupling (all via the generic engine, no hardcoded wiring):
  Chiller --feeds--> AirHandler --suppliesAirTo--> Zone
  * Chiller reads its cooling demand from the AHUs it feeds (ctx.outputs) and
    publishes chilled-water supply temp + electrical power.
  * AHU reads chilled-water temp from the chiller (ctx.inputs) and the zone return
    temp from the zone it serves (ctx.outputs), and publishes supply-air temp +
    airflow + filter ΔP + fan power.

Engineering bases:
  * Chiller COP bounded by Carnot:  COP_carnot = T_evap/(T_cond − T_evap)  (Kelvin)
                                    COP = η2 · COP_carnot · partload_factor
                                    P_elec = Q_cool / COP
  * Fan affinity laws:  Q ∝ N,  ΔP ∝ N²,  P_fan ∝ N³
  * Coil:  Q_coil = ṁ_air · cp · (T_return − T_supply)
  * Filter loading:  ΔP = ΔP_clean + k · dust ;  dust += c · flow · dt
"""

from __future__ import annotations

import math
from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
CP_AIR = 1005.0
RHO_AIR = 1.2

SIG_PWR = CFP + "activePower"
SIG_COP = CFP + "chillerCOP"
SIG_CHWS = CFP + "chwSupplyTemp"        # chilled water supply temp (°C)
SIG_SUPPLY = CFP + "supplyAirTemp"
SIG_AIRFLOW = CFP + "flowRate"
SIG_DP = CFP + "filterDeltaP"
SIG_TEMP = CFP + "temperature"
SIG_COOLDEMAND = CFP + "coolingDemandKW"


class ChillerModel(DynamicsModel):
    archetype = "ThermalTransferDevice"
    models = [CFP + "Chiller"]
    produces = [SIG_COP, SIG_PWR, SIG_CHWS]
    consumes = ["THERMAL demand (downstream AHUs)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"chws": ctx.fnum("chwSetpoint", 7.0)},
                           signals={SIG_CHWS: ctx.fnum("chwSetpoint", 7.0)})

    def step(self, ctx, state):
        # cooling demand = sum of downstream AHUs' coolingDemandKW (1-tick lag)
        loads = [s for sts in ctx.outputs.values() for s in sts]
        q_cool = flows.sum_signal(loads, SIG_COOLDEMAND)
        rated_kw = ctx.fnum("ratedCapacity", 500.0)        # cooling kW
        q_cool = min(q_cool if q_cool > 0 else ctx.fnum("baseCoolingKW", 0.3 * rated_kw),
                     rated_kw)
        load_frac = q_cool / max(rated_kw, 1.0)

        chw_set = ctx.fnum("chwSetpoint", 7.0)             # evaporator °C
        t_cond = ctx.fnum("condenserTemp", 35.0)           # condenser °C
        T_evap_K = chw_set + 273.15
        T_cond_K = t_cond + 273.15
        cop_carnot = T_evap_K / max(T_cond_K - T_evap_K, 1.0)
        eta2 = ctx.fnum("secondLawEff", 0.5)
        # part-load curve: COP peaks ~50–70% load (IPLV-shaped)
        plf = 0.6 + 0.6 * load_frac - 0.4 * load_frac * load_frac
        cond = ctx.fnum("conditionIndex", 1.0)             # fouling lowers COP
        cop = eta2 * cop_carnot * max(plf, 0.2) * (0.85 + 0.15 * cond)
        cop += ctx.rng.gauss(0, 0.03)
        cop = max(1.5, cop)

        p_elec = q_cool / cop if cop else 0.0              # kW electrical
        # chilled-water supply tracks setpoint unless overloaded
        chws = chw_set + max(0.0, (load_frac - 1.0)) * 5.0 + ctx.rng.gauss(0, 0.05)
        state.internal["chws"] = chws
        state.status = "degraded" if load_frac > 1.0 or cop < 3.0 else "running"
        state.signals = {SIG_COP: round(cop, 2), SIG_PWR: round(p_elec, 2),
                         SIG_CHWS: round(chws, 2)}
        return state


class AirHandlerModel(DynamicsModel):
    archetype = "AirHandler"
    models = [CFP + "AirHandlingUnit", CFP + "PrecisionCooler",
              CFP + "FanCoilUnit", "https://ontology.nextxr.io/v3/hvac#AirHandler"]
    produces = [SIG_SUPPLY, SIG_AIRFLOW, SIG_DP, SIG_PWR, SIG_COOLDEMAND]
    consumes = ["THERMAL chilled water (upstream chiller)", "AIR served zone (downstream)"]

    def init_state(self, ctx):
        sp = ctx.fnum("setpoint", 22.0)
        return EntityState(status="running",
                           internal={"dust": 0.0, "supply": sp - 8.0},
                           signals={SIG_SUPPLY: sp - 8.0})

    def step(self, ctx, state):
        setpoint = ctx.fnum("setpoint", 22.0)              # zone target °C
        # served zone return temp = the zone we supply (downstream), 1-tick lag
        served = [s for sts in ctx.outputs.values() for s in sts]
        t_return = flows.first_signal(served, SIG_TEMP, default=setpoint)

        # chilled water available from upstream chiller
        chw_sources = flows.upstream_with_signal(ctx, SIG_CHWS)
        chw_temp = flows.first_signal(chw_sources, SIG_CHWS,
                                      default=ctx.fnum("chwSetpoint", 7.0))

        # fan: modulate speed toward meeting the zone (simple proportional control)
        err = t_return - setpoint
        speed = _clip(0.3 + 0.5 * err, 0.2, 1.0)           # fraction of rated
        rated_flow = ctx.fnum("ratedAirflowLps", 2000.0)   # L/s
        flow = rated_flow * speed                          # affinity: Q ∝ N
        rated_fan_kw = ctx.fnum("ratedFanKW", 15.0)
        fan_kw = rated_fan_kw * speed ** 3                 # affinity: P ∝ N³

        # coil capacity: how cold can supply get given chw temp + coil effectiveness
        eps = ctx.fnum("coilEffectiveness", 0.7)
        t_supply_min = t_return - eps * (t_return - chw_temp)
        t_supply = max(t_supply_min, setpoint - ctx.fnum("maxSupplyDelta", 10.0))
        # first-order lag toward target supply
        prev = state.internal.get("supply", t_supply)
        tau = ctx.fnum("supplyTauSec", 60.0)
        t_supply = prev + (t_supply - prev) * min(1.0, ctx.dt / tau) + ctx.rng.gauss(0, 0.05)
        state.internal["supply"] = t_supply

        # cooling delivered = chiller demand this AHU places
        m_dot = (flow / 1000.0) * RHO_AIR
        q_cool_kw = max(0.0, m_dot * CP_AIR * (t_return - t_supply)) / 1000.0

        # filter dust loading -> ΔP rise
        dust = state.internal.get("dust", 0.0) + ctx.fnum("dustRate", 1e-6) * flow * ctx.dt
        state.internal["dust"] = dust
        dp = ctx.fnum("filterDPClean", 120.0) + ctx.fnum("filterDPk", 5e4) * dust
        dp += ctx.rng.gauss(0, 2.0)

        state.status = "degraded" if (err > 3.0 or dp > 300) else "running"
        state.signals = {
            SIG_SUPPLY: round(t_supply, 2), SIG_AIRFLOW: round(flow, 1),
            SIG_DP: round(dp, 1), SIG_PWR: round(fan_kw, 2),
            SIG_COOLDEMAND: round(q_cool_kw, 2),
        }
        return state


def _clip(x, lo, hi):
    return max(lo, min(hi, x))
