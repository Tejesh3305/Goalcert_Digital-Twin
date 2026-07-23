// api.js — the api surface the ported Collins components (EVWorld, NetworkMap)
// expect, implemented on top of the v3 platform client (/api/v1). Keeps the
// Collins components byte-for-byte while feeding them the live v3 twin.
import v3 from '../api/client'

export const api = {
  // Fleet / tram network geometry + live vehicles (fleet CURIEs match v3's pack).
  twinNetwork: (tenant) => v3.twinNetwork(tenant),

  // Per-asset status blurb used by EVWorld hotspots — cosmetic, so any failure
  // resolves to an empty object rather than throwing inside the render loop.
  assetStatus: async (body) => {
    try { return await v3.copilot.asset(body) } catch { return {} }
  },
}

export default api
