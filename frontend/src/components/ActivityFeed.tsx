import { useState } from 'react'
import type { JournalItem } from '../types'
import { ActivityRow } from './ActivityRow'
import { ConditionsPanel } from './ConditionsPanel'

type Tab = 'all' | 'signals' | 'fills' | 'recon' | 'setup'

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'signals', label: 'Signals' },
  { id: 'fills', label: 'Fills' },
  { id: 'recon', label: 'Recon' },
  { id: 'setup', label: 'Setup' },
]

interface Props {
  signals: JournalItem[]
  fills: JournalItem[]
  reconciles: JournalItem[]
  activeSymbol?: string
}

export function ActivityFeed({ signals, fills, reconciles, activeSymbol }: Props) {
  const [tab, setTab] = useState<Tab>('all')

  const merged = [...signals, ...fills, ...reconciles]
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    .slice(0, 60)

  const list =
    tab === 'all' ? merged
    : tab === 'signals' ? signals
    : tab === 'fills' ? fills
    : tab === 'recon' ? reconciles
    : []

  return (
    <div className="bg-panel border border-border flex flex-col overflow-hidden animate-fade-up">
      <div className="px-4 pt-4 shrink-0">
        <div className="text-[10px] font-mono tracking-[0.12em] uppercase text-faint mb-3">Activity</div>
        <div className="flex gap-4 border-b border-border">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`text-[11px] pb-2.5 -mb-px border-b-2 transition-colors ${
                tab === t.id ? 'text-accent-ink border-accent' : 'text-faint border-transparent hover:text-dim'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="feed flex-1 overflow-y-auto">
        {tab === 'setup' ? (
          <ConditionsPanel activeSymbol={activeSymbol} />
        ) : list.length === 0 ? (
          <div className="px-5 py-5 text-faint text-xs">No entries yet</div>
        ) : (
          list.map((item, i) => <ActivityRow key={`${item.kind}-${item.ts}-${i}`} item={item} />)
        )}
      </div>
    </div>
  )
}
