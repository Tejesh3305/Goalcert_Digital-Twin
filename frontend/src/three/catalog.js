/**
 * catalog.js — the transparent, single-source manifest of every 3-D asset.
 *
 * This is the registry the Asset Library reads from. Each entry maps a prop
 * `key` (the same key used in props.jsx's PROP_FOR and in scene nodes'
 * geometry.prop) to human-readable metadata: a label, the category it belongs
 * to, which facility it's relevant for, and a short description.
 *
 * Real-world dimensions are NOT hand-typed here — they are computed at runtime
 * from each built model's bounding box × PROP_SCALE (see assetDims() below), so
 * what the Library shows is always exactly what renders. That keeps the pipeline
 * honest while you review proportions for the VR walk-through.
 */
import * as THREE from 'three'
import { buildProp } from './props'

// Keep in sync with Scene.jsx — 1 prop unit ≈ 0.32 m.
export const PROP_SCALE = 0.32

// Display order of categories in the Library.
export const CATEGORIES = [
  'Data Center / IT',
  'Cooling / HVAC',
  'Power',
  'Water / Mechanical',
  'Fire / Safety / Security',
  'Access / Vertical Transport',
  'Lighting',
  'Hospital — Imaging',
  'Hospital — Patient Care',
  'Hospital — Surgical',
  'Hospital — Support Services',
  'Factory / Logistics',
  'Office / Furniture',
  'Residential',
]

// facility tags: 'datacenter', 'hospital', 'generic' (both / any facility)
export const CATALOG = [
  /* ── Data Center / IT ───────────────────────────────────────────── */
  { key: 'rack', label: 'Server Rack (42U)', cat: 'Data Center / IT', facility: ['datacenter'], desc: 'Full-height 42U rack with individual server/switch/storage faceplates, activity LEDs, top fan tray and cable management.' },
  { key: 'smallrack', label: 'Half-Height Rack', cat: 'Data Center / IT', facility: ['datacenter'], desc: 'Compact wall/edge rack for branch IT or comms cabinets.' },
  { key: 'network', label: 'Network Switch (48-port)', cat: 'Data Center / IT', facility: ['datacenter'], desc: '1RU top-of-rack switch with SFP cage and per-port link LEDs.' },
  { key: 'pdu', label: 'Power Distribution Unit', cat: 'Data Center / IT', facility: ['datacenter'], desc: 'Vertical 0U rack PDU with metered outlet LEDs.' },
  { key: 'cabletray', label: 'Overhead Cable Tray', cat: 'Data Center / IT', facility: ['datacenter'], desc: 'Ladder-style overhead tray carrying colour-coded cable bundles.' },
  { key: 'inrowcooler', label: 'In-Row Cooling Unit', cat: 'Data Center / IT', facility: ['datacenter'], desc: 'Narrow chilled-water cooler that sits between racks in a hot/cold aisle.' },

  /* ── Cooling / HVAC ─────────────────────────────────────────────── */
  { key: 'crac', label: 'CRAC Unit', cat: 'Cooling / HVAC', facility: ['datacenter'], desc: 'Computer-room air conditioner — perimeter downflow cooling with 3 axial fans.' },
  { key: 'chiller', label: 'Chiller', cat: 'Cooling / HVAC', facility: ['datacenter', 'generic'], desc: 'Air-cooled chiller with condenser coils and twin top fans.' },
  { key: 'coolingtower', label: 'Cooling Tower', cat: 'Cooling / HVAC', facility: ['datacenter', 'generic'], desc: 'Open cooling tower with induced-draught fan.' },
  { key: 'ahu', label: 'Air Handling Unit', cat: 'Cooling / HVAC', facility: ['generic'], desc: 'Central AHU with supply fan and duct connections.' },
  { key: 'vav', label: 'VAV Box', cat: 'Cooling / HVAC', facility: ['generic'], desc: 'Variable-air-volume terminal box with damper.' },
  { key: 'boiler', label: 'Boiler', cat: 'Cooling / HVAC', facility: ['generic'], desc: 'Hot-water boiler with burner and flue.' },
  { key: 'splitac', label: 'Split AC Indoor Unit', cat: 'Cooling / HVAC', facility: ['generic'], desc: 'Wall-mounted split air-conditioner head.' },

  /* ── Power ──────────────────────────────────────────────────────── */
  { key: 'ups', label: 'UPS (Tower)', cat: 'Power', facility: ['datacenter'], desc: 'Uninterruptible power supply with LCD and battery status.' },
  { key: 'battery', label: 'Battery Cabinet', cat: 'Power', facility: ['datacenter'], desc: 'UPS battery string cabinet with per-module status LEDs.' },
  { key: 'switchgear', label: 'Switchgear', cat: 'Power', facility: ['datacenter', 'generic'], desc: 'LV switchgear line-up with metering displays.' },
  { key: 'transformer', label: 'Transformer', cat: 'Power', facility: ['datacenter', 'generic'], desc: 'Dry-type distribution transformer with bushings.' },
  { key: 'generator', label: 'Standby Generator', cat: 'Power', facility: ['datacenter', 'generic'], desc: 'Diesel genset on skid with radiator and exhaust.' },
  { key: 'meter', label: 'Energy Meter', cat: 'Power', facility: ['generic'], desc: 'Wall-mounted metering panel.' },
  { key: 'solar', label: 'Solar Panel', cat: 'Power', facility: ['generic'], desc: 'Tilted PV panel on frame.' },
  { key: 'evcharger', label: 'EV Charger', cat: 'Power', facility: ['generic'], desc: 'EV charging pedestal with screen and connector.' },

  /* ── Water / Mechanical ─────────────────────────────────────────── */
  { key: 'pump', label: 'Pump Set', cat: 'Water / Mechanical', facility: ['generic'], desc: 'Horizontal centrifugal pump with motor.' },
  { key: 'watertank', label: 'Water Tank', cat: 'Water / Mechanical', facility: ['generic'], desc: 'Domed storage tank with level ladder.' },
  { key: 'valve', label: 'Valve Assembly', cat: 'Water / Mechanical', facility: ['generic'], desc: 'Gate valve with handwheel on pipe.' },
  { key: 'compressor', label: 'Air Compressor', cat: 'Water / Mechanical', facility: ['generic'], desc: 'Reciprocating compressor with receiver tank.' },
  { key: 'tank', label: 'Storage Tank', cat: 'Water / Mechanical', facility: ['generic'], desc: 'Vertical process tank on legs.' },

  /* ── Fire / Safety / Security ───────────────────────────────────── */
  { key: 'cleanagent', label: 'Clean-Agent Suppression', cat: 'Fire / Safety / Security', facility: ['datacenter'], desc: 'FM-200 / Novec cylinder bank with discharge manifold and nozzle — gaseous fire suppression for IT spaces.' },
  { key: 'extinguisher', label: 'Fire Extinguisher', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Portable extinguisher with hose.' },
  { key: 'firepanel', label: 'Fire Alarm Panel', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Addressable fire alarm control panel.' },
  { key: 'smoke', label: 'Smoke Detector', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Ceiling smoke/heat detector.' },
  { key: 'sprinkler', label: 'Sprinkler Head', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Pendant fire sprinkler.' },
  { key: 'exitsign', label: 'Exit Sign', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Illuminated emergency exit sign.' },
  { key: 'sensor', label: 'IoT Sensor', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'Environmental sensor puck.' },
  { key: 'camera', label: 'Security Camera', cat: 'Fire / Safety / Security', facility: ['generic'], desc: 'CCTV camera on mount.' },

  /* ── Access / Vertical Transport ────────────────────────────────── */
  { key: 'door', label: 'Access Door', cat: 'Access / Vertical Transport', facility: ['generic'], desc: 'Secure door with vision panel and status light.' },
  { key: 'elevator', label: 'Elevator Car', cat: 'Access / Vertical Transport', facility: ['generic'], desc: 'Elevator cab with doors and floor indicator.' },
  { key: 'escalator', label: 'Escalator', cat: 'Access / Vertical Transport', facility: ['generic'], desc: 'Escalator run with glass balustrade.' },

  /* ── Lighting ───────────────────────────────────────────────────── */
  { key: 'light', label: 'Linear Light Fixture', cat: 'Lighting', facility: ['generic'], desc: 'Suspended linear luminaire.' },
  { key: 'ceilinglight', label: 'Ceiling Light Panel', cat: 'Lighting', facility: ['generic'], desc: 'Recessed ceiling light (emits a point light in scene).' },
  { key: 'surgicallight', label: 'OR Surgical Light', cat: 'Lighting', facility: ['hospital'], desc: 'Ceiling pendant LED surgical light cluster with sterile handles.' },

  /* ── Hospital — Imaging ─────────────────────────────────────────── */
  { key: 'mri', label: 'MRI Scanner', cat: 'Hospital — Imaging', facility: ['hospital'], desc: 'Wide-bore MRI gantry (bore Ø70 cm) with patient table and operator panel.' },
  { key: 'ctscanner', label: 'CT Scanner', cat: 'Hospital — Imaging', facility: ['hospital'], desc: 'CT gantry (bore Ø70 cm) with tilt arc, laser guides and motorised couch.' },
  { key: 'xray', label: 'X-Ray (C-Arm)', cat: 'Hospital — Imaging', facility: ['hospital'], desc: 'Floor-mounted X-ray with C-arm tube head and flat-panel detector.' },
  { key: 'ultrasound', label: 'Ultrasound Machine', cat: 'Hospital — Imaging', facility: ['hospital'], desc: 'Cart-based ultrasound with articulating LCD, control console and 3 probes.' },

  /* ── Hospital — Patient Care ────────────────────────────────────── */
  { key: 'hospitalbed', label: 'ICU Hospital Bed', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Articulated ICU bed: side rails, IV pole, call button, braking castors.' },
  { key: 'bed', label: 'Ward Bed', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Simple ward bed with headboard.' },
  { key: 'stretcher', label: 'Stretcher / Trolley', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Transport stretcher with IV pole and castors.' },
  { key: 'wheelchair', label: 'Wheelchair', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Manual wheelchair.' },
  { key: 'examtable', label: 'Examination Table', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Padded exam couch with paper roll, drawers and pull-out step.' },
  { key: 'patientmonitor', label: 'Patient Monitor', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Vitals monitor on pole with live ECG/SpO₂ traces and parameter panels.' },
  { key: 'ivstand', label: 'IV Stand', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'IV pole with saline bag, drip chamber and tubing.' },
  { key: 'infusionpump', label: 'Infusion Pump', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Twin volumetric infusion pumps on a slim IV pole.' },
  { key: 'ventilator', label: 'ICU Ventilator', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Mechanical ventilator with touch-screen, circuit ports and breathing hose.' },
  { key: 'dialysis', label: 'Dialysis Machine', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Haemodialysis machine with screen, blood pump, dialyzer and blood lines.' },
  { key: 'crashcart', label: 'Crash Cart', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Emergency code cart with colour-coded drawers, defibrillator and O₂ cylinder.' },
  { key: 'medcart', label: 'Medication Cart', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Automated dispensing cart (Pyxis-style) with touch-screen and locked drawers.' },
  { key: 'fridge', label: 'Medical Fridge', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Glass-door medical refrigerator for medicines/samples.' },
  { key: 'bloodbank', label: 'Blood Bank / Cold Storage', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Twin glass-door blood-bank refrigerator with digital temperature readout and over-temp alarm.' },
  { key: 'gas', label: 'Medical Gas Panel', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Wall medical-gas outlet panel (O₂ / N₂O / vacuum).' },
  { key: 'nurse', label: 'Nurse Station Terminal', cat: 'Hospital — Patient Care', facility: ['hospital'], desc: 'Nurse call / monitoring terminal.' },

  /* ── Hospital — Surgical ────────────────────────────────────────── */
  { key: 'operatingtable', label: 'Operating Table', cat: 'Hospital — Surgical', facility: ['hospital'], desc: 'Articulated OR table with padded sections, arm boards and column base.' },
  { key: 'anesthesia', label: 'Anesthesia Machine', cat: 'Hospital — Surgical', facility: ['hospital'], desc: 'Anaesthesia workstation: vaporizers, flowmeters, bellows, monitor and circuit.' },
  { key: 'mayostand', label: 'Mayo Instrument Stand', cat: 'Hospital — Surgical', facility: ['hospital'], desc: 'Cantilever instrument tray stand for the sterile field.' },

  /* Hospital — Support Services */
  { key: 'autoclave', label: 'Autoclave / Steriliser', cat: 'Hospital — Support Services', facility: ['hospital'], desc: 'CSSD steam steriliser: stainless chamber, circular pressure door, temperature/pressure gauges and cycle-status LED.' },
  { key: 'gascylinderbank', label: 'Medical Gas Cylinder Bank', cat: 'Hospital — Support Services', facility: ['hospital'], desc: 'Bulk O₂ / N₂O manifold: two banks of colour-shouldered cylinders on a frame with automatic changeover control and pressure display.' },
  { key: 'laf', label: 'Laminar Flow Unit', cat: 'Hospital — Surgical', facility: ['hospital'], desc: 'Ceiling laminar-airflow canopy for OR / clean areas.' },

  /* ── Factory / Logistics ────────────────────────────────────────── */
  { key: 'robot', label: 'Industrial Robot Arm', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Articulated robot arm (animated).' },
  { key: 'conveyor', label: 'Conveyor', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Belt conveyor with moving cargo.' },
  { key: 'cnc', label: 'CNC Machine', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Enclosed CNC machining centre.' },
  { key: 'welder', label: 'Welding Cell', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Robotic welder with arc tip.' },
  { key: 'press', label: 'Hydraulic Press', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Stamping press with moving ram.' },
  { key: 'forklift', label: 'Forklift', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Counterbalance forklift.' },
  { key: 'palletrack', label: 'Pallet Racking', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Warehouse pallet rack with loads.' },
  { key: 'workbench', label: 'Workbench', cat: 'Factory / Logistics', facility: ['generic'], desc: 'Industrial workbench with pegboard.' },

  /* ── Office / Furniture ─────────────────────────────────────────── */
  { key: 'desk', label: 'Desk + Monitor', cat: 'Office / Furniture', facility: ['generic'], desc: 'Office desk with monitor and keyboard.' },
  { key: 'chair', label: 'Office Chair', cat: 'Office / Furniture', facility: ['generic'], desc: 'Task chair on castor base.' },
  { key: 'stool', label: 'Stool', cat: 'Office / Furniture', facility: ['generic'], desc: 'Height stool.' },
  { key: 'sofa', label: 'Sofa', cat: 'Office / Furniture', facility: ['generic'], desc: 'Three-seat sofa with cushions.' },
  { key: 'table', label: 'Table', cat: 'Office / Furniture', facility: ['generic'], desc: 'Meeting / dining table.' },
  { key: 'coffeetable', label: 'Coffee Table', cat: 'Office / Furniture', facility: ['generic'], desc: 'Low coffee table.' },
  { key: 'bookshelf', label: 'Bookshelf', cat: 'Office / Furniture', facility: ['generic'], desc: 'Shelf unit with books.' },
  { key: 'cabinet', label: 'Cabinet', cat: 'Office / Furniture', facility: ['generic'], desc: 'Storage cabinet with handles.' },
  { key: 'plant', label: 'Indoor Plant', cat: 'Office / Furniture', facility: ['generic'], desc: 'Potted foliage plant.' },
  { key: 'watercooler', label: 'Water Cooler', cat: 'Office / Furniture', facility: ['generic'], desc: 'Bottled water dispenser.' },
  { key: 'reception', label: 'Reception Desk', cat: 'Office / Furniture', facility: ['generic'], desc: 'Reception counter with accent panel.' },
  { key: 'locker', label: 'Lockers', cat: 'Office / Furniture', facility: ['generic'], desc: 'Bank of steel lockers.' },
  { key: 'whiteboard', label: 'Whiteboard', cat: 'Office / Furniture', facility: ['generic'], desc: 'Wall whiteboard.' },
  { key: 'tv', label: 'Wall Display', cat: 'Office / Furniture', facility: ['generic'], desc: 'Large wall-mounted screen.' },
  { key: 'monitor', label: 'Monitor', cat: 'Office / Furniture', facility: ['generic'], desc: 'Desktop monitor on stand.' },
  { key: 'printer', label: 'Printer / MFP', cat: 'Office / Furniture', facility: ['generic'], desc: 'Multifunction office printer.' },

  /* ── Residential ────────────────────────────────────────────────── */
  { key: 'kitchen', label: 'Kitchen Counter', cat: 'Residential', facility: ['generic'], desc: 'Kitchen base + wall units with worktop.' },
  { key: 'stove', label: 'Stove / Range', cat: 'Residential', facility: ['generic'], desc: 'Cooking range with hob.' },
  { key: 'sink', label: 'Sink / Vanity', cat: 'Residential', facility: ['generic'], desc: 'Wash basin with tap.' },
  { key: 'toilet', label: 'Toilet', cat: 'Residential', facility: ['generic'], desc: 'WC with cistern.' },
  { key: 'shower', label: 'Shower', cat: 'Residential', facility: ['generic'], desc: 'Glass shower enclosure.' },
  { key: 'bathtub', label: 'Bathtub', cat: 'Residential', facility: ['generic'], desc: 'Bathtub.' },
  { key: 'nightstand', label: 'Nightstand', cat: 'Residential', facility: ['generic'], desc: 'Bedside table.' },
  { key: 'rug', label: 'Rug', cat: 'Residential', facility: ['generic'], desc: 'Floor rug.' },
  { key: 'car', label: 'Car', cat: 'Residential', facility: ['generic'], desc: 'Passenger car (for garages / driveways).' },
  { key: 'box', label: 'Generic Box / Crate', cat: 'Residential', facility: ['generic'], desc: 'Fallback box prop for unmapped assets.' },
]

/** All catalog entries grouped by category, in CATEGORIES order. */
export function catalogByCategory() {
  const byCat = {}
  for (const c of CATEGORIES) byCat[c] = []
  for (const item of CATALOG) (byCat[item.cat] ||= []).push(item)
  return CATEGORIES.map((cat) => ({ cat, items: byCat[cat] })).filter((g) => g.items.length)
}

/**
 * Compute real-world dimensions (metres) of a built prop, plus debug stats.
 * Builds the prop once with throwaway materials, measures its bounding box, and
 * scales by PROP_SCALE. Disposes everything afterwards.
 */
export function assetDims(key, M) {
  const obj = buildProp(key, M)
  const bbox = new THREE.Box3().setFromObject(obj)
  const size = new THREE.Vector3(); bbox.getSize(size)
  let meshes = 0, tris = 0
  obj.traverse((o) => {
    if (o.isMesh) {
      meshes++
      const g = o.geometry
      if (g?.index) tris += g.index.count / 3
      else if (g?.attributes?.position) tris += g.attributes.position.count / 3
    }
  })
  // dispose geometries (materials are shared M — left intact)
  obj.traverse((o) => { if (o.geometry) o.geometry.dispose() })
  const m = (v) => Math.round(v * PROP_SCALE * 100) / 100   // metres, 2dp
  const ft = (v) => Math.round(v * PROP_SCALE * 3.28084 * 10) / 10
  return {
    w: m(size.x), h: m(size.y), d: m(size.z),
    wFt: ft(size.x), hFt: ft(size.y), dFt: ft(size.z),
    meshes, tris: Math.round(tris),
  }
}
