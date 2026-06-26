/**
 * DemoTwin — renders a pre-baked archviz scene for a domain, with NO backend.
 *
 * While the live 2-D→3-D pipeline is being perfected in the standalone
 * `apps/2d-to-3d` workspace, the real app shows these static demo scenes
 * (generated from the same scene engine, furnished + lit). BimViewer makes no
 * network calls when given a `scene` and no `tenant`, so this is fully offline.
 */
import { useMemo } from 'react'
import BimViewer from './BimViewer'
import residential from '../three/demoScenes/residential.json'
import hospital from '../three/demoScenes/hospital.json'
import datacenter from '../three/demoScenes/datacenter.json'
import office from '../three/demoScenes/office.json'
import factory from '../three/demoScenes/factory.json'

const SCENES = { residential, hospital, datacenter, office, factory }

export function resolveDomain(domain = '', name = '') {
  const d = `${domain} ${name}`.toLowerCase()
  if (/hospital|clinic|health|ward|patient/.test(d)) return 'hospital'
  if (/data\s?cent|datacenter|server/.test(d)) return 'datacenter'
  if (/factory|manufact|plant|warehouse|production/.test(d)) return 'factory'
  if (/office|corporate|workplace|hvac/.test(d)) return 'office'
  return 'residential'
}

export default function DemoTwin({ domain, name }) {
  const scene = useMemo(() => SCENES[resolveDomain(domain, name)] || residential, [domain, name])
  return <BimViewer scene={scene} />
}
