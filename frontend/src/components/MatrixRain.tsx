import { useEffect, useRef } from 'react'

const POOL = '0123456789ABCDEF<>[]{}=+-|$#%^&*@!'

const FS    = 14
const COL_W = 16

type Cell = { ch: string; alpha: number; target: number; ttl: number }

const rch = () => POOL[Math.floor(Math.random() * POOL.length)]

interface Props {
  active?: boolean
}

export function MatrixRain({ active = true }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let grid: Cell[][]
    let W: number, H: number, cols: number, rows: number

    const setup = () => {
      W    = window.innerWidth
      H    = window.innerHeight
      cols = Math.floor(W / COL_W)
      rows = Math.ceil(H / FS) + 1

      canvas.width  = W
      canvas.height = H

      grid = Array.from({ length: cols }, () =>
        Array.from({ length: rows }, () => ({
          ch:     rch(),
          alpha:  0,
          target: Math.random() < 0.12 ? 0.08 + Math.random() * 0.18 : 0,
          ttl:    Math.floor(Math.random() * 300),
        }))
      )
    }

    setup()
    window.addEventListener('resize', setup)

    let frameId: number

    const draw = () => {
      ctx.fillStyle = '#000'
      ctx.fillRect(0, 0, W, H)

      ctx.font         = `bold ${FS}px "JetBrains Mono",monospace`
      ctx.textBaseline = 'top'
      ctx.fillStyle    = '#00ff41'

      for (let c = 0; c < cols; c++) {
        const x = c * COL_W
        for (let r = 0; r < rows; r++) {
          const cell = grid[c][r]

          cell.alpha += (cell.target - cell.alpha) * 0.04

          if (--cell.ttl <= 0) {
            cell.ch     = rch()
            cell.target = Math.random() < 0.12
              ? 0.08 + Math.random() * 0.18
              : 0
            cell.ttl    = 80 + Math.floor(Math.random() * 300)
          }

          if (cell.alpha > 0.005) {
            ctx.globalAlpha = cell.alpha
            ctx.fillText(cell.ch, x, r * FS)
          }
        }
      }

      ctx.globalAlpha = 1
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
        filter: active ? 'brightness(1)' : 'brightness(0.12) saturate(0.3)',
        transition: 'filter 2.5s ease-in-out',
      }}
    />
  )
}
