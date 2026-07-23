"""
sensor_defaults.py — the observable property each sensor class observes.

`nxr:SensorShape` requires every Sensor to declare `sosa:observes`. That is an
ontology fact, not a caller concern: nothing creating a TemperatureSensor should
have to know it observes `cfp:temperature`. So both write paths auto-inject it —

    server/write_api.py   the REST create-entity endpoint
    agents/twin_agents.py the agent flow (Validator + Graph Writer)

This map used to live only in write_api.py, so sensors created through the REST
API validated while identical sensors drafted by the Build-a-Twin agent failed
the SHACL gate with "A Sensor must declare at least one observable property".
One definition, both paths.

`ontology_ref=True` on the injected Rel matters: the target is an ontology
concept IRI, not a graph node, so the writer must skip its Neo4j integrity check
and render the raw IRI into the validation TTL.
"""
from __future__ import annotations

CFP = "https://ontology.nextxr.io/v3/cfp#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"

SENSOR_OBSERVES: dict[str, str] = {
    f"{CFP}TemperatureSensor": "cfp:temperature",
    f"{CFP}HumiditySensor": "cfp:relativeHumidity",
    f"{CFP}AirQualitySensor": "cfp:airQualityIndex",
    f"{CFP}OccupancySensor": "cfp:occupancyCount",
    f"{CFP}LightSensor": "cfp:illuminance",
    f"{CFP}VibrationSensor": "cfp:vibrationVelocity",
    f"{CFP}NoiseSensor": "cfp:soundLevel",
    f"{CFP}WaterQualitySensor": "cfp:waterQuality",
    f"{CFP}SmokeDetector": "cfp:smokeObscuration",
    f"{CFP}AspiratingDetector": "cfp:smokeObscuration",
    f"{CFP}HeatDetector": "cfp:temperature",
    f"{CFP}FlameDetector": "cfp:flameSignal",
    f"{CFP}Camera": "cfp:illuminance",
    f"{CFP}AccessReader": "cfp:doorState",
    f"{CFP}IntrusionSensor": "cfp:doorState",
    f"{CFP}LeakSensor": "cfp:leakState",
    f"{CFP}WaterMeter": "cfp:flowRate",
    f"{CFP}EnergyMeter": "cfp:energy",
    f"{HVAC}TemperatureSensor": "cfp:temperature",
}

# Fallback when a Sensor subclass isn't in the map above: without SOME observable
# property the entity cannot pass the shape at all, and a generic reading is a
# better outcome than refusing to build the twin.
DEFAULT_OBSERVES = "cfp:temperature"


def observes_for(canonical_type: str) -> str | None:
    """The observable-property CURIE for a sensor class, or None if the class is
    not a sensor (in which case nothing should be injected)."""
    if not canonical_type:
        return None
    if canonical_type in SENSOR_OBSERVES:
        return SENSOR_OBSERVES[canonical_type]
    local = canonical_type.split("#")[-1]
    if local.endswith("Sensor") or local.endswith("Detector") or local.endswith("Meter"):
        return DEFAULT_OBSERVES
    return None


def inject_observes(canonical_type: str, rels: list) -> list:
    """Append the `sosa:observes` Rel for a sensor class if the caller didn't
    supply one. Returns the (possibly extended) list; non-sensors pass through.
    """
    from graph.writer import Rel

    if any(getattr(r, "predicate", None) == "sosa:observes" for r in rels):
        return rels
    target = observes_for(canonical_type)
    if target:
        rels = list(rels) + [Rel(predicate="sosa:observes", target_id=target,
                                 ontology_ref=True)]
    return rels
