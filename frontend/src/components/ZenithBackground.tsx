/**
 * Fixed, non-interactive atmospheric background: ambient blue/violet glow pools
 * plus four hard-edged geometric corner triangles and a top light streak.
 * Sits at z-index 0; all app content sits above it. Replaces the MatrixRain
 * canvas — pure CSS, no animation loop, no per-frame work.
 */
export function ZenithBackground() {
  return (
    <div aria-hidden className="fixed inset-0 z-0 overflow-hidden pointer-events-none">
      {/* ambient glow pools */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 60% 50% at -10% 5%, rgba(20,62,200,0.42) 0%, transparent 56%),' +
            'radial-gradient(ellipse 48% 52% at 112% 100%, rgba(88,28,135,0.26) 0%, transparent 54%),' +
            'radial-gradient(ellipse 35% 35% at 50% 50%, rgba(7,12,23,0.5) 0%, transparent 72%)',
        }}
      />
      {/* top-right royal-blue triangle */}
      <div
        className="absolute"
        style={{
          top: -120, right: -100, width: 460, height: 460,
          background: 'conic-gradient(from 220deg at 100% 0%, rgba(20,62,200,0.66) 0deg, rgba(29,78,216,0.42) 36deg, transparent 64deg)',
          clipPath: 'polygon(100% 0%, 100% 100%, 0% 0%)',
        }}
      />
      <div
        className="absolute"
        style={{
          top: -60, right: -50, width: 250, height: 250,
          background: 'linear-gradient(150deg, rgba(96,165,250,0.24) 0%, transparent 54%)',
          clipPath: 'polygon(100% 0%, 100% 100%, 0% 0%)',
        }}
      />
      {/* bottom-right violet triangle */}
      <div
        className="absolute"
        style={{
          bottom: -110, right: -80, width: 380, height: 380,
          background: 'conic-gradient(from 40deg at 100% 100%, rgba(76,29,149,0.46) 0deg, transparent 72deg)',
          clipPath: 'polygon(100% 100%, 0% 100%, 100% 0%)',
        }}
      />
      {/* bottom-left echo */}
      <div
        className="absolute"
        style={{
          bottom: -80, left: -60, width: 240, height: 240,
          background: 'linear-gradient(50deg, rgba(20,62,200,0.18) 0%, transparent 56%)',
          clipPath: 'polygon(0% 100%, 100% 100%, 0% 0%)',
        }}
      />
      {/* top light streak */}
      <div
        className="absolute top-0 left-0 right-0"
        style={{
          height: 1,
          background: 'linear-gradient(90deg, transparent 0%, rgba(37,99,235,0.42) 32%, rgba(99,102,241,0.30) 66%, transparent 100%)',
        }}
      />
    </div>
  )
}
