"""hospital — hospital-campus digital-twin domain (machine-twin runtime).

Exposes one self-describing SPEC ("hospital-campus") the runtime can twin: the
whole campus (departments, beds, medical gas, water, power, sterilisation,
infection control, patient flow) with a live set of clinical views (bed board,
OR schedule, patient-flow funnel, infection map, medical-gas schematic).

This is distinct from the CFP `packs/hospital` functional pack (dynamics
archetypes + monitoring bindings) used by the plan→3D / feed path — this runs in
the machine-twin runtime like railway-metro / turbine / edm.
"""
from .physics import (
    HospitalCampusPhysics, SIGNALS, UNITS, redlines, FAULTS,
    hospital_hvac_pressure, medical_gas_hydraulics, sterilisation_f0,
    cold_chain_thermal, infection_spread_wellsriley, power_resilience,
    legionella_thermal, patient_flow_queuing,
)
from .predict import component_health, predict
from .behaviors import build_hospital_registry

SPEC = {
    "key": "hospital-campus",
    "label": "Hospital Campus",
    "class_iri": "https://ontology.nextxr.io/v3/hospital#Hospital",
    "control": "patient_load",
    "physics": HospitalCampusPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_hospital_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["or_pressure"]: ("OR Positive Pressure", "Pa"),
        SIGNALS["iso_pressure"]: ("Isolation Pressure", "Pa"),
        SIGNALS["air_changes"]: ("Air Changes / Hour", "ACH"),
        SIGNALS["autoclave_f0"]: ("Autoclave F0", "min"),
        SIGNALS["o2_pressure"]: ("Medical O2 Pressure", "kPa"),
        SIGNALS["n2o_pressure"]: ("Medical N2O Pressure", "kPa"),
        SIGNALS["gas_reserve"]: ("Medical Gas Reserve", "%"),
        SIGNALS["bloodbank_temp"]: ("Blood Bank Temp", "°C"),
        SIGNALS["infection_risk"]: ("Infection Probability", "%"),
        SIGNALS["quanta"]: ("Quanta Concentration", "q/m³"),
        SIGNALS["ups_soc"]: ("UPS State of Charge", "%"),
        SIGNALS["ups_runtime"]: ("UPS Runtime", "min"),
        SIGNALS["gen_ready"]: ("Generator Ready", ""),
        SIGNALS["power_load"]: ("Critical Power Load", "%"),
        SIGNALS["hot_water"]: ("Hot Water Return", "°C"),
        SIGNALS["cold_water"]: ("Cold Water Supply", "°C"),
        SIGNALS["dead_legs"]: ("Legionella Dead-Legs", ""),
        SIGNALS["ed_arrivals"]: ("ED Arrival Rate", "/h"),
        SIGNALS["ed_wait"]: ("ED Wait Time", "min"),
        SIGNALS["patients"]: ("Patients In System", ""),
        SIGNALS["bed_occ"]: ("Bed Occupancy", "%"),
        SIGNALS["or_util"]: ("OR Utilisation", "%"),
    },
    "subsystems": [
        ("clinical_environment", "Clinical Environment"),
        ("medical_gas", "Medical Gas"),
        ("cold_chain", "Cold Chain"),
        ("power_resilience", "Power Resilience"),
        ("water_safety", "Water Safety"),
        ("patient_flow", "Patient Flow"),
    ],
    "checks": {
        SIGNALS["or_pressure"]: (redlines.or_pressure_min, "below"),
        SIGNALS["air_changes"]: (redlines.air_changes_min, "below"),
        SIGNALS["autoclave_f0"]: (redlines.autoclave_f0_min, "below"),
        SIGNALS["o2_pressure"]: (redlines.o2_pressure_min, "below"),
        SIGNALS["gas_reserve"]: (redlines.gas_reserve_min, "below"),
        SIGNALS["bloodbank_temp"]: (redlines.bloodbank_max, "above"),
        SIGNALS["ups_runtime"]: (redlines.ups_runtime_min, "below"),
        SIGNALS["power_load"]: (redlines.power_load_max, "above"),
        SIGNALS["hot_water"]: (redlines.hot_water_min, "below"),
        SIGNALS["dead_legs"]: (redlines.dead_legs_max, "above"),
        SIGNALS["infection_risk"]: (redlines.infection_risk_max, "above"),
        SIGNALS["ed_wait"]: (redlines.ed_wait_max, "above"),
        SIGNALS["bed_occ"]: (redlines.bed_occ_max, "above"),
    },
}

SPECS = [SPEC]

__all__ = [
    "HospitalCampusPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
    "hospital_hvac_pressure", "medical_gas_hydraulics", "sterilisation_f0",
    "cold_chain_thermal", "infection_spread_wellsriley", "power_resilience",
    "legionella_thermal", "patient_flow_queuing",
    "component_health", "predict", "build_hospital_registry", "SPEC", "SPECS",
]
