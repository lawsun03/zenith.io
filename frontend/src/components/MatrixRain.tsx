import { useEffect, useRef } from 'react'

// Hex digits + scattered punctuation keeps it "trading terminal" rather than
// pure sci-fi. The mix of 0-9 and A-F reads as raw hex data.
const CHARS = '0123456789ABCDEF0123456789ABCDEF<>[]{}=+-|$@!?#%^&*01'

const FS = 13       // font size px
const SPEED = 0.6   // rows per frame — lower = slower fall
const FADE = 0.055  // opacity of black overlay per frame — controls trail length

export function MatrixRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let drops: number[]
    let cols: number
    let W: number, H: number

    const setup = () => {
      W = window.innerWidth
      H = window.innerHeight
      canvas.width = W
      canvas.height = H
      cols = Math.floor(W / FS)
      // Stagger starting positions so all columns don't fall in sync
      drops = Array.from({ length: cols }, () =>
        Math.floor(Math.random() * -(H / FS))
      )
      // Clear to solid black on resize so old content doesn't ghost
      ctx.fillStyle = '#000000'
      ctx.fillRect(0, 0, W, H)
    }

    setup()
    window.addEventListener('resize', setup)

    let frameId: number

    const draw = () => {
      // Semi-transparent black overlay fades old characters → trail effect
      ctx.fillStyle = `rgba(0,0,0,${FADE})`
      ctx.fillRect(0, 0, W, H)

      ctx.font = `${FS}px "JetBrains Mono",monospace`

      for (let c = 0; c < cols; c++) {
        const row = Math.floor(drops[c])
        const x = c * FS
        const y = row * FS

        if (y >= 0 && y <= H + FS) {
          const ch = CHARS[Math.floor(Math.random() * CHARS.length)]

          // Leading character: near-white green glow
          ctx.fillStyle = '#ccffdd'
          ctx.fillText(ch, x, y)

          // One step behind: full bright green
          if (row > 0) {
            const prevCh = CHARS[Math.floor(Math.random() * CHARS.length)]
            ctx.fillStyle = '#00ff41'
            ctx.fillText(prevCh, x, (row - 1) * FS)
          }
        }

        // Reset column randomly after it passes the bottom
        if (drops[c] * FS > H && Math.random() > 0.975) {
          drops[c] = Math.floor(Math.random() * -(H / FS / 3))
        }

        drops[c] += SPEED
      }

      frameId = requestAnimationFrame(draw)
    }

    frameId = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(frameId)
      window.removeEventListener('resize', setup)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: -1,
        pointerEvents: 'none',
      }}
    />
  )
}
