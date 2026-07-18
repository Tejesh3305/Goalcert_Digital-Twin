import { createElement, Component } from 'react'
import { createRoot } from 'react-dom/client'
import TwinRemoteApp from './TwinRemoteApp'

// Top-level safety net. Because the remote now renders under its own react-dom root
// (not the host's), a render error here can no longer be caught by the hub's error
// boundary — it would silently blank this panel. This restores the fallback the hub
// used to provide (TwinBoundary), on the remote side where the error now lives.
class RootBoundary extends Component {
  constructor(props) { super(props); this.state = { err: null } }
  static getDerivedStateFromError(err) { return { err } }
  componentDidCatch(err) { console.error('[twin remote] render error', err) }
  render() {
    if (this.state.err) {
      return createElement('div', {
        style: {
          minHeight: '40vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
          padding: 24, textAlign: 'center', color: '#aab0e0',
          fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 13,
        },
      }, 'The Digital Twin view hit an error. Switch views or reload to retry.')
    }
    return this.props.children
  }
}

// Self-mounting federation entry for the Goalcert Hub.
//
// The hub host renders a plain <div> and calls mount(div, props). We spin up our
// OWN react-dom root inside that div and render the twin app into it. Crucially,
// that root is driven by THIS bundle's React + react-dom — never the host's — so
// every hook in the twin subtree (including @react-three/fiber's react-reconciler
// and its useThree/useGLTF hooks) resolves to a single, consistent React.
//
// Why this exists: the previous design exposed <TwinRemoteApp/> as a component and
// let the host's react-dom render it. Under @originjs/vite-plugin-federation the
// component's hooks then went through the shared React (the host's copy) while
// R3F's bundled react-reconciler stayed bound to this remote's React — two React
// dispatchers in one tree → "invalid hook call" (React #321), which blanked the
// 3-D canvas inside the hub while it rendered fine standalone. A DOM boundary
// removes the shared-React coupling entirely.
//
// Returns a handle so the host can push route changes (update) and clean up
// (unmount) without re-importing the remote.
export function mount(el, initialProps = {}) {
  const root = createRoot(el)
  let props = initialProps
  const render = () => root.render(createElement(RootBoundary, null, createElement(TwinRemoteApp, props)))
  render()
  return {
    update(next) {
      props = { ...props, ...next }
      render()
    },
    unmount() {
      try { root.unmount() } catch { /* already torn down */ }
    },
  }
}

export default mount
