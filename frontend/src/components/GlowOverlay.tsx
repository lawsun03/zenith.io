interface Props {
  active: boolean
}

/**
 * Full-screen edge glow that signals bot readiness.
 *
 * Two layers cross-fade on a 2.5s transition:
 *   - Active:  bright green phosphor glow, breathing pulse animation
 *   - Standby: near-black, barely-visible border — "screen on but sleeping"
 *
 * Both sit above the MatrixRain canvas (z-index -1) but below all UI (z-index 998).
 * pointer-events: none so neither layer captures clicks.
 */
export function GlowOverlay({ active }: Props) {
  return (
    <>
      {/* Active layer — green phosphor glow, breathing */}
      <div
        className="screen-glow-active"
        style={{
          position: 'fixed',
          inset: 0,
          pointerEvents: 'none',
          zIndex: 998,
          opacity: active ? 1 : 0,
          transition: 'opacity 2.5s ease-in-out',
        }}
      />
      {/* Standby layer — dim, static, always behind active layer */}
      <div
        className="screen-glow-standby"
        style={{
          position: 'fixed',
          inset: 0,
          pointerEvents: 'none',
          zIndex: 997,
          opacity: active ? 0 : 1,
          transition: 'opacity 2.5s ease-in-out',
        }}
      />
    </>
  )
}
