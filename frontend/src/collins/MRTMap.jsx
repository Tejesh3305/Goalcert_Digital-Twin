// MRTMap.jsx — Singapore MRT network schematic map.
//
// Renders a simplified transit diagram of Singapore's MRT network with:
//   - Line selector pills (NSL, EWL, CCL, DTL, TEL, NEL)
//   - Station nodes (interchange = larger white circle)
//   - Animated train dots moving along each line
//   - Station click → inspector panel with telemetry
//   - Works in sim mode (random train positions) and live mode (from physics)
import React, { useEffect, useMemo, useState } from 'react'
import { Icon, SIG, fmt, sevClass } from './lib.jsx'

// ── MRT line definitions ───────────────────────────────────────────
const LINES = [
  { id: 'NSL', name: 'North-South Line', color: '#E2231A', operator: 'SMRT',
    stations: ['Jurong East','Bukit Batok','Bukit Gombak','Choa Chu Kang','Yew Tee','Kranji','Marsiling','Woodlands','Admiralty','Sembawang','Canberra','Yishun','Khatib','Ang Mo Kio','Bishan','Braddell','Toa Payoh','Novena','Newton','Orchard','Somerset','Dhoby Ghaut','City Hall','Raffles Place','Marina Bay','Marina South Pier'],
    // Simplified SVG path (schematic, not geographic)
    points: [[15,8],[15,14],[15,19],[10,24],[7,27],[5,30],[5,36],[10,40],[15,40],[20,38],[25,36],[28,32],[28,27],[30,22],[35,18],[38,18],[42,20],[46,22],[50,24],[52,28],[52,33],[52,38],[52,44],[52,50],[52,56],[52,62]],
  },
  { id: 'EWL', name: 'East-West Line', color: '#009645', operator: 'SMRT',
    stations: ['Tuas Link','Tuas West Road','Tuas Crescent','Gul Circle','Joo Koon','Pioneer','Boon Lay','Lakeside','Chinese Garden','Jurong East','Clementi','Dover','Buona Vista','Commonwealth','Queenstown','Redhill','Tiong Bahru','Outram Park','Tanjong Pagar','Raffles Place','City Hall','Bugis','Lavender','Kallang','Aljunied','Paya Lebar','Eunos','Kembangan','Bedok','Tanah Merah','Simei','Tampines','Pasir Ris','Changi Airport','Expo'],
    points: [[2,50],[5,50],[8,50],[10,48],[12,46],[14,44],[16,42],[18,40],[17,38],[15,35],[20,32],[24,30],[28,30],[32,30],[36,30],[40,30],[44,32],[48,36],[50,40],[52,44],[52,50],[56,52],[60,52],[64,52],[68,50],[72,48],[76,46],[78,44],[80,42],[82,40],[84,38],[86,36],[90,36],[94,38],[92,36]],
  },
  { id: 'NEL', name: 'North-East Line', color: '#9900AA', operator: 'SBS Transit',
    stations: ['HarbourFront','Outram Park','Chinatown','Clarke Quay','Dhoby Ghaut','Little India','Farrer Park','Boon Keng','Potong Pasir','Woodleigh','Serangoon','Kovan','Hougang','Buangkok','Sengkang','Punggol'],
    points: [[40,62],[48,58],[50,54],[52,50],[52,44],[54,40],[56,38],[58,36],[60,34],[62,30],[64,26],[66,24],[68,22],[72,20],[76,18],[80,16]],
  },
  { id: 'CCL', name: 'Circle Line', color: '#FA9E0D', operator: 'SMRT',
    stations: ['Dhoby Ghaut','Bras Basah','Esplanade','Promenade','Nicoll Highway','Stadium','Mountbatten','Dakota','Paya Lebar','MacPherson','Tai Seng','Bartley','Serangoon','Lorong Chuan','Bishan','Marymount','Caldecott','Botanic Gardens','Farrer Road','Holland Village','Buona Vista','one-north','Kent Ridge','Haw Par Villa','Pasir Panjang','Labrador Park','Telok Blangah','HarbourFront'],
    points: [[52,44],[55,44],[58,46],[62,48],[65,48],[68,48],[70,46],[72,44],[72,42],[70,38],[68,34],[66,30],[64,26],[60,22],[55,18],[50,16],[46,16],[42,18],[38,20],[34,24],[28,28],[24,30],[22,34],[22,38],[24,42],[28,46],[34,50],[40,54]],
  },
  { id: 'DTL', name: 'Downtown Line', color: '#005EC4', operator: 'SBS Transit',
    stations: ['Bukit Panjang','Cashew','Hillview','Beauty World','King Albert Park','Sixth Avenue','Tan Kah Kee','Botanic Gardens','Stevens','Newton','Little India','Rochor','Bugis','Promenade','Bayfront','Downtown','Telok Ayer','Chinatown','Fort Canning','Bencoolen','Jalan Besar','Bendemeer','Geylang Bahru','Mattar','MacPherson','Ubi','Kaki Bukit','Bedok North','Bedok Reservoir','Tampines West','Tampines','Tampines East','Upper Changi','Expo'],
    points: [[8,14],[12,14],[16,16],[20,18],[24,20],[28,22],[32,22],[36,20],[40,22],[44,24],[48,28],[50,32],[52,36],[56,40],[58,44],[58,48],[56,50],[54,52],[52,52],[52,48],[54,44],[56,40],[58,36],[62,34],[66,32],[70,32],[74,32],[78,34],[80,36],[82,36],[86,36],[88,36],[90,38],[92,36]],
  },
  { id: 'TEL', name: 'Thomson-East Coast', color: '#9D5B25', operator: 'SMRT',
    stations: ['Woodlands North','Woodlands','Woodlands South','Springleaf','Lentor','Mayflower','Bright Hill','Upper Thomson','Caldecott','Mount Pleasant','Stevens','Napier','Orchard Boulevard','Orchard','Great World','Havelock','Outram Park','Maxwell','Shenton Way','Marina Bay','Gardens by the Bay','Tanjong Rhu','Katong Park','Tanjong Katong','Marine Parade','Marine Terrace','Siglap','Bayshore'],
    points: [[10,36],[10,40],[14,42],[18,38],[22,34],[26,30],[30,24],[34,20],[38,18],[42,20],[44,22],[46,26],[48,28],[50,30],[50,34],[50,38],[48,42],[48,46],[50,50],[52,54],[56,58],[60,58],[64,56],[68,54],[72,52],[76,50],[80,48],[84,46]],
  },
]

// Interchange stations (shared between lines)
const INTERCHANGES = new Set([
  'Jurong East','Buona Vista','Dhoby Ghaut','City Hall','Raffles Place','Bishan',
  'Serangoon','Paya Lebar','Outram Park','Bugis','Newton','Botanic Gardens',
  'MacPherson','HarbourFront','Chinatown','Little India','Marina Bay','Caldecott',
  'Stevens','Orchard','Tampines','Woodlands','Promenade','Expo',
])

// ── Simulated train positions ──────────────────────────────────────
function simTrains(lines, tick) {
  const trains = []
  for (const line of lines) {
    const count = Math.max(3, Math.floor(line.stations.length / 4))
    for (let i = 0; i < count; i++) {
      const base = (i / count + tick * 0.0003 * (1 + i * 0.1)) % 1
      const speed = 40 + Math.random() * 40
      trains.push({
        id: `${line.id}-T${String(i + 1).padStart(2, '0')}`,
        line: line.id,
        progress: base,
        speed: Math.round(speed),
        status: Math.random() > 0.95 ? 'delayed' : 'ok',
        pax: Math.round(400 + Math.random() * 800),
      })
    }
  }
  return trains
}

// Interpolate point along polyline
function pointAt(points, t) {
  if (!points || points.length < 2) return [0, 0]
  const segs = []
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    const len = Math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]) || 0.01
    segs.push(len); total += len
  }
  let d = Math.max(0, Math.min(1, t)) * total
  for (let i = 0; i < segs.length; i++) {
    if (d <= segs[i]) {
      const k = d / segs[i]
      return [
        points[i][0] + (points[i + 1][0] - points[i][0]) * k,
        points[i][1] + (points[i + 1][1] - points[i][1]) * k,
      ]
    }
    d -= segs[i]
  }
  return points[points.length - 1]
}

// Station position = its fraction along the line
function stationPositions(line) {
  const n = line.stations.length
  return line.stations.map((name, i) => {
    const t = n > 1 ? i / (n - 1) : 0
    const [x, y] = pointAt(line.points, t)
    return { name, x, y, t, lineId: line.id }
  })
}

export default function MRTMap({ twin, height = 480 }) {
  const [activeLine, setActiveLine] = useState(null) // null = show all
  const [sel, setSel] = useState(null)
  const [tick, setTick] = useState(0)

  // Animate trains
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 1500)
    return () => clearInterval(iv)
  }, [])

  const trains = useMemo(() => simTrains(LINES, tick), [tick])
  const visibleLines = activeLine ? LINES.filter(l => l.id === activeLine) : LINES

  // All station positions
  const allStations = useMemo(() => {
    const map = new Map()
    for (const line of LINES) {
      for (const s of stationPositions(line)) {
        if (!map.has(s.name)) map.set(s.name, { ...s, lines: [line.id] })
        else map.get(s.name).lines.push(line.id)
      }
    }
    return [...map.values()]
  }, [])

  const visibleTrains = activeLine ? trains.filter(t => t.line === activeLine) : trains
  const inService = visibleTrains.filter(t => t.status === 'ok').length
  const delayed = visibleTrains.filter(t => t.status === 'delayed').length

  // KPIs from twin telemetry (if available)
  const otp = twin?.latest?.['rail:networkOTP']
  const headway = twin?.latest?.['rail:headway']
  const paxLoad = twin?.latest?.['rail:passengerLoad']

  return (
    <div className="netmap" style={{ height, position: 'relative' }}>
      <svg viewBox="-2 -2 100 72" style={{ width: '100%', height: '100%', display: 'block' }}
        onClick={() => setSel(null)}>

        {/* Line polylines */}
        {LINES.map(line => {
          const dim = activeLine && activeLine !== line.id
          const pts = line.points.map(([x, y]) => `${x},${y}`).join(' ')
          return (
            <g key={line.id} opacity={dim ? 0.08 : 1}>
              <polyline points={pts} fill="none" stroke={line.color}
                strokeWidth={activeLine === line.id ? 1.4 : 0.8}
                strokeLinecap="round" strokeLinejoin="round" />
            </g>
          )
        })}

        {/* Stations */}
        {allStations.map(s => {
          const isInterchange = INTERCHANGES.has(s.name)
          const dim = activeLine && !s.lines.includes(activeLine)
          if (dim) return null
          return (
            <g key={s.name} style={{ cursor: 'pointer' }}
              onClick={e => { e.stopPropagation(); setSel({ kind: 'station', station: s }) }}>
              <circle cx={s.x} cy={s.y} r={isInterchange ? 0.7 : 0.32}
                fill={isInterchange ? '#fff' : '#8b93a3'}
                stroke={isInterchange ? '#333' : 'none'}
                strokeWidth={isInterchange ? 0.2 : 0} />
              {isInterchange && (
                <text x={s.x} y={s.y - 1.2} textAnchor="middle"
                  fontSize={1.4} fill="var(--text, #333)" fontWeight="600"
                  style={{ pointerEvents: 'none' }}>
                  {s.name.length > 12 ? s.name.slice(0, 10) + '…' : s.name}
                </text>
              )}
              <title>{s.name} ({s.lines.join(', ')})</title>
            </g>
          )
        })}

        {/* Animated trains */}
        {visibleTrains.map(t => {
          const line = LINES.find(l => l.id === t.line)
          if (!line) return null
          const [x, y] = pointAt(line.points, t.progress)
          return (
            <g key={t.id} style={{ cursor: 'pointer', transition: 'transform 1.4s linear' }}
              transform={`translate(${x},${y})`}
              onClick={e => { e.stopPropagation(); setSel({ kind: 'train', train: t, line }) }}>
              {t.status === 'delayed' && (
                <circle r={1.2} fill="none" stroke="#e11d48" strokeWidth={0.3} className="netmap-pulse" />
              )}
              <circle r={0.55} fill={line.color} stroke="#fff" strokeWidth={0.18} />
              <title>{t.id} · {t.speed} km/h · {t.pax} pax</title>
            </g>
          )
        })}
      </svg>

      {/* Header overlay */}
      <div className="netmap-head">
        <span className="v-chip"><Icon n="ti-train" /> <b>Singapore MRT</b></span>
        {otp != null && <span className="netmap-stat">OTP <b>{fmt(otp)}%</b></span>}
        {headway != null && <span className="netmap-stat">Headway <b>{fmt(headway)}s</b></span>}
        <span className="netmap-stat"><b>{inService}</b> trains</span>
        {delayed > 0 && (
          <span className="netmap-stat" style={{ color: 'var(--accent-red)' }}>
            <Icon n="ti-alert-triangle" /> <b>{delayed}</b> delayed
          </span>
        )}
      </div>

      {/* Line selector pills */}
      <div className="netmap-rail">
        <button className={`netmap-chip ${!activeLine ? 'on' : ''}`}
          style={{ '--c': '#7c3aed' }}
          onClick={() => setActiveLine(null)}>
          All
        </button>
        {LINES.map(l => (
          <button key={l.id} className={`netmap-chip ${activeLine === l.id ? 'on' : ''}`}
            style={{ '--c': l.color }}
            onClick={() => setActiveLine(activeLine === l.id ? null : l.id)}>
            <i style={{ background: l.color }} />{l.id}
          </button>
        ))}
      </div>

      {/* Inspector panel */}
      {sel && (
        <div className="netmap-inspect" onClick={e => e.stopPropagation()}>
          {sel.kind === 'train' && <>
            <div className="t"><i style={{ background: sel.line.color }} /> {sel.train.id}</div>
            <div className="r"><span>Line</span><b>{sel.line.name}</b></div>
            <div className="r"><span>Speed</span><b>{sel.train.speed} km/h</b></div>
            <div className="r"><span>Passengers</span><b>{sel.train.pax}</b></div>
            <div className="r"><span>Status</span>
              <b style={{ color: sel.train.status === 'delayed' ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                {sel.train.status}
              </b>
            </div>
          </>}
          {sel.kind === 'station' && <>
            <div className="t"><Icon n="ti-map-pin" /> {sel.station.name}</div>
            <div className="r"><span>Type</span><b>{INTERCHANGES.has(sel.station.name) ? 'Interchange' : 'Station'}</b></div>
            <div className="r"><span>Lines</span><b>{sel.station.lines.join(', ')}</b></div>
            <div className="r"><span>Trains nearby</span>
              <b>{trains.filter(t => t.line === sel.station.lineId && Math.abs(t.progress - sel.station.t) < 0.08).length}</b>
            </div>
          </>}
          <button className="x" onClick={() => setSel(null)}>×</button>
        </div>
      )}
    </div>
  )
}
