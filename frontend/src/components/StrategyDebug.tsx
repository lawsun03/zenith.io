import { useState } from 'react'
import type { StrategyStatePayload } from '../types'

interface Props {
  state: StrategyStatePayload | null
}

export function StrategyDebug({ state }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <div className="border border-border font-mono text-sm">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2 text-dim hover:text-ink text-xs tracking-widest uppercase"
      >
        <span>Strategy State</span>
        <span className="text-accent">{open ? '▼' : '▶'}</span>
      </button>

      {open && (
        <div className="border-t border-border p-4 flex flex-col gap-6">

          {/* KZ Levels */}
          <div>
            <div className="text-dim text-xs tracking-widest uppercase mb-2">KZ Levels</div>
            {!state || Object.keys(state.kz_ranges).length === 0 ? (
              <div className="text-dim text-xs">No levels locked today</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim">
                    <th className="text-left pr-6 pb-1">Zone</th>
                    <th className="text-right pr-6 pb-1">High</th>
                    <th className="text-right pb-1">Low</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(state.kz_ranges).map(([name, { high, low }]) => (
                    <tr key={name}>
                      <td className="text-ink pr-6 py-0.5">{name}</td>
                      <td className="text-right pr-6 py-0.5">
                        <span className="text-ink">{high}</span>
                        {state.kz_pending_a.includes(`${name}_high`) && (
                          <span className="ml-1 text-accent text-[10px]">[A]</span>
                        )}
                      </td>
                      <td className="text-right py-0.5">
                        <span className="text-ink">{low}</span>
                        {state.kz_pending_a.includes(`${name}_low`) && (
                          <span className="ml-1 text-accent text-[10px]">[A]</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Awaiting Sweeps */}
          <div>
            <div className="text-dim text-xs tracking-widest uppercase mb-2">Awaiting Sweeps</div>
            {!state || state.awaiting_sweeps.length === 0 ? (
              <div className="text-dim text-xs">No pending sweeps</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim">
                    <th className="text-left pr-4 pb-1">Side</th>
                    <th className="text-left pr-4 pb-1">Src</th>
                    <th className="text-right pr-4 pb-1">Price</th>
                    <th className="text-right pr-4 pb-1">Bars</th>
                    <th className="text-left pb-1">Zone</th>
                  </tr>
                </thead>
                <tbody>
                  {state.awaiting_sweeps.map((s, i) => (
                    <tr key={i}>
                      <td className={`pr-4 py-0.5 ${s.side === 'high' ? 'text-red-400' : 'text-accent'}`}>
                        {s.side}
                      </td>
                      <td className="text-dim pr-4 py-0.5">
                        {s.source === 'kz_level' ? 'kz' : 'sw'}
                      </td>
                      <td className="text-ink text-right pr-4 py-0.5">{s.price}</td>
                      <td className="text-dim text-right pr-4 py-0.5">{s.bars_elapsed}</td>
                      <td className="text-dim py-0.5">{s.killzone}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

        </div>
      )}
    </div>
  )
}
