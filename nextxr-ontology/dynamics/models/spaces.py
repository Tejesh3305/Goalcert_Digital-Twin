"""
spaces.py — thermal/occupancy models for spaces (the COUPLING MEDIUM).

A Zone/Room is where HVAC, IT heat, occupancy, and the outdoors meet. Modelling it
is what makes the rest couple: the AHU cools the zone, servers/people heat it, and
sensors + server inlet temps read from it. Without this, every entity is an island
again.

Lumped-capacitance (single-node) thermal balance — the standard 1st-order building
zone model (ASHRAE fundamentals; RC network, R1C1):

    C_z · dT/dt = Q_internal + Q_solar + Q_envelope − Q_hvac

  Q_internal  = Σ heat from contained equipment (servers, lights) + people·~100 W
  Q_envelope  = UA · (T_out − T_z)              conduction through the envelope
  Q_hvac      = ṁ_supply · cp_air · (T_z − T_supply)   sensible cooling delivered
  C_z         = ρ_air · V · cp_air · thermal_mass_factor   zone heat capacity

CO2 and humidity follow simple mass balances driven by occupancy + ventilation.
"""

from __future__ import annotations

from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
CP_AIR = 1005.0      # J/(kg·K)
RHO_AIR = 1.2        # kg/m3

SIG_TEMP = CFP + "temperature"
SIG_RH = CFP + "relativeHumidity"
SIG_CO2 = CFP + "airQualityIndex"   # reuse AQI channel for CO2 ppm
SIG_OCC = CFP + "occupancyCount"
SIG_SUPPLY = CFP + "supplyAirTemp"  # produced by AHU/FCU models
SIG_AIRFLOW = CFP + "flowRate"      # supply airflow (L/s) from the AHU


class ZoneThermalModel(DynamicsModel):
    archetype = "SpaceThermal"
    models = [CFP + "Zone", CFP + "Room", CFP + "Floor"]
    produces = [SIG_TEMP, SIG_RH, SIG_CO2, SIG_OCC]
    consumes = ["AIR (supply from AHU)", "SPATIAL (contained heat sources)"]

    def init_state(self, ctx: EntityContext) -> EntityState:
        t0 = ctx.fnum("initialTemp", 22.0)
        return EntityState(status="running", signals={
            SIG_TEMP: t0, SIG_RH: 50.0, SIG_CO2: 450.0, SIG_OCC: 0.0},
            internal={"temp": t0, "co2": 450.0})

    def step(self, ctx: EntityContext, state: EntityState) -> EntityState:
        # --- geometry / capacity ---
        area = ctx.fnum("areaM2", 100.0)
        height = ctx.fnum("ceilingHeightM", 3.0)
        vol = max(1.0, area * height)
        C_z = RHO_AIR * vol * CP_AIR * ctx.fnum("thermalMassFactor", 5.0)

        T = state.internal.get("temp", 22.0)
        T_out = ctx.fnum("outdoorTemp", self._diurnal_outdoor(ctx))
        UA = ctx.fnum("envelopeUA", 0.5 * area)        # W/K

        # --- occupancy (diurnal + noise), bounded by capacity ---
        occ_cap = ctx.fnum("occupancyCap", max(1.0, area / 10.0))
        occ = self._diurnal_occupancy(ctx, occ_cap)

        # --- internal gains from contained equipment + people + lights ---
        q_equip = 0.0
        for st in ctx.contained:
            # any contained model that publishes activePower dumps ~that as heat
            q_equip += st.signals.get(CFP + "activePower", 0.0) * 1000.0  # kW->W
            q_equip += st.signals.get(CFP + "heatOutputW", 0.0)
        q_people = occ * ctx.fnum("wattsPerPerson", 100.0)
        q_lights = area * ctx.fnum("lightingWperM2", 8.0)
        Q_internal = q_equip + q_people + q_lights
        Q_envelope = UA * (T_out - T)

        # --- HVAC delivered cooling: from upstream AIR sources (AHU/FCU) ---
        air_sources = flows.upstream_by_flow(ctx, flows.Flow.AIR)
        Q_hvac = 0.0
        for st in air_sources:
            t_supply = st.signals.get(SIG_SUPPLY)
            flow_ls = st.signals.get(SIG_AIRFLOW, 0.0)
            if t_supply is None or flow_ls <= 0:
                continue
            m_dot = (flow_ls / 1000.0) * RHO_AIR        # kg/s
            Q_hvac += m_dot * CP_AIR * (T - t_supply)   # >0 when supply is cooler

        # --- integrate temperature ---
        dT = (Q_internal + Q_envelope - Q_hvac) / C_z * ctx.dt
        T = T + dT + ctx.rng.gauss(0, 0.02)             # tiny turbulence noise

        # --- CO2 mass balance: people add, ventilation flushes toward outdoor ---
        co2 = state.internal.get("co2", 450.0)
        vent_ls = sum(st.signals.get(SIG_AIRFLOW, 0.0) for st in air_sources)
        ach = (vent_ls / 1000.0) / vol * 3600.0 if vol else 0.0   # air changes/hr
        co2_gen = occ * 0.0052 / max(vol, 1.0) * 1e6 * (ctx.dt / 3600.0)  # ppm rise
        co2 += co2_gen - (ach * ctx.dt / 3600.0) * (co2 - 420.0)
        co2 = max(400.0, co2)

        state.internal["temp"] = T
        state.internal["co2"] = co2
        rh = 50.0 + (occ / max(occ_cap, 1.0)) * 10.0 + ctx.rng.gauss(0, 0.5)
        state.signals = {SIG_TEMP: round(T, 2), SIG_RH: round(rh, 1),
                         SIG_CO2: round(co2, 0), SIG_OCC: round(occ)}
        return state

    # ---- helpers ----
    def _hour(self, ctx) -> float:
        return (ctx.t / 3600.0) % 24.0

    def _diurnal_occupancy(self, ctx, cap) -> float:
        import math
        h = self._hour(ctx)
        # peak ~13:00, near-zero overnight
        frac = max(0.0, math.sin((h - 6.0) / 12.0 * math.pi)) if 6 <= h <= 20 else 0.0
        return cap * frac * (0.9 + 0.1 * ctx.rng.random())

    def _diurnal_outdoor(self, ctx) -> float:
        import math
        h = self._hour(ctx)
        return 24.0 + 6.0 * math.sin((h - 9.0) / 24.0 * 2 * math.pi)  # ~18–30°C
