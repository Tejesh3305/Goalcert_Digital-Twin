import { useState } from 'react'

/**
 * Goalcert brand lockup.
 *
 * The mark prefers a user-supplied PNG and degrades gracefully:
 *   1. /goalcert-mark.png   ← drop your icon-only PNG here
 *   2. /goalcert-mark.svg   ← bundled fallback (also the favicon)
 *
 * If you'd rather use the full PNG lockup (mark + wordmark in one image),
 * drop it at /goalcert-logo.png and render <Logo variant="image" />.
 *
 *   variant="full"  → mark + "Goalcert" wordmark (+ optional tagline)
 *   variant="mark"  → icon only
 *   variant="image" → the full /goalcert-logo.png lockup as a single image
 */
export function BrandMark({ size = 30, className = '' }) {
  const [src, setSrc] = useState('/goalcert-mark.png')
  return (
    <img
      src={src}
      onError={() => setSrc((s) => (s !== '/goalcert-mark.svg' ? '/goalcert-mark.svg' : s))}
      alt="Goalcert"
      className={`brand-mark ${className}`}
      style={{ width: size, height: size }}
      draggable={false}
    />
  )
}

export default function Logo({ variant = 'full', size = 30, showTag = true, className = '' }) {
  if (variant === 'image') {
    return (
      <img src="/goalcert-logo.png" alt="Goalcert — Workforce Intelligence"
           className={`brand ${className}`} style={{ height: size }} draggable={false} />
    )
  }
  if (variant === 'mark') {
    return <span className={`brand ${className}`}><BrandMark size={size} /></span>
  }
  return (
    <span className={`brand ${className}`}>
      <BrandMark size={size} />
      <span className="brand-word">
        <span className="brand-name">Goalcert</span>
        {showTag && <span className="brand-tag">Workforce Intelligence</span>}
      </span>
    </span>
  )
}
