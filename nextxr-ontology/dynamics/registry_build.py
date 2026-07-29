"""
registry_build.py — assemble the DynamicsRegistry with all known models.

Mirrors server.main._build_registry() for behaviours. Register most-specific
models; the DefaultEquipmentModel on cfp:FacilityEquipment is the subclass-aware
fallback for everything not yet modelled.

As you write a model for a new class, import and register it here — one line.
"""

from __future__ import annotations

from dynamics.model import DynamicsRegistry
from dynamics.models.datacenter import ComputeRackModel
from dynamics.models.default import DefaultEquipmentModel
from dynamics.models.electrical import TransformerModel, UPSModel, UtilityFeedModel
from dynamics.models.fluid import FluidMoverModel
from dynamics.models.hospital import (
    AutoclaveModel,
    BedModel,
    GasManifoldModel,
    ImagingDeviceModel,
    InfusionPumpModel,
    RefrigeratedUnitModel,
    VentilatorModel,
)
from dynamics.models.hvac import AirHandlerModel, ChillerModel
from dynamics.models.it import ServerModel
from dynamics.models.power_extra import AggregatorModel, PrimeMoverModel
from dynamics.models.sensing import BinaryEventSourceModel, DerivedObserverModel
from dynamics.models.spaces import ZoneThermalModel
from dynamics.models.transport import DiscreteTransportModel
from dynamics.models.water import StorageVesselModel


def build_dynamics_registry() -> DynamicsRegistry:
    r = DynamicsRegistry()
    # spaces (the coupling medium)
    r.register(ZoneThermalModel())            # SpaceThermal
    # electrical chain
    r.register(UtilityFeedModel())            # PowerSource
    r.register(TransformerModel())            # ElectricalConverter
    r.register(UPSModel())                    # EnergyStore
    r.register(PrimeMoverModel())             # PrimeMover (generator)
    r.register(AggregatorModel())             # Aggregator (meter/panel/circuit)
    # hvac plant
    r.register(ChillerModel())                # ThermalTransferDevice
    r.register(AirHandlerModel())             # AirHandler
    r.register(FluidMoverModel())             # FluidMover (pump/fan)
    # water
    r.register(StorageVesselModel())          # StorageVessel (tank)
    # compute / ICT
    r.register(ServerModel())                 # ComputeLoad
    # sensing
    r.register(BinaryEventSourceModel())      # BinaryEventSource (smoke/leak/door/...)
    r.register(DerivedObserverModel())        # DerivedObserver (env sensors)
    # transport
    r.register(DiscreteTransportModel())      # DiscreteTransport (elevator/escalator)
    # domain-specific (hospital + datacenter)
    r.register(RefrigeratedUnitModel())       # RefrigeratedUnit (cold-chain fridge / blood bank)
    r.register(GasManifoldModel())            # GasManifold (medical gas)
    r.register(ImagingDeviceModel())          # ImagingDevice (MRI/CT)
    r.register(VentilatorModel())             # Ventilator (ICU / transport)
    r.register(InfusionPumpModel())           # InfusionPump (volumetric / syringe)
    r.register(AutoclaveModel())              # Autoclave (CSSD steriliser)
    r.register(BedModel())                    # Bed (smart hospital bed)
    r.register(ComputeRackModel())            # ComputeRack (data-centre rack)
    # universal fallback
    r.register(DefaultEquipmentModel())       # DefaultEquipment
    return r
