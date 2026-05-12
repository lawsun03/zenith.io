import type { JournalItem } from '../types'
import type { ReactNode } from 'react'

interface Props {
  title: string
  items: JournalItem[]
  renderItem: (item: JournalItem, i: number) => ReactNode
  empty?: string
}

export function FeedSection({ title, items, renderItem, empty }: Props) {
  return (
    <section className="bg-panel">
      <header className="border-b border-border px-4 py-2 flex items-baseline justify-between">
        <h2 className="text-[10px] tracking-[0.3em] text-dim uppercase">{title}</h2>
        <span className="text-xs text-dim tabular-nums">{items.length}</span>
      </header>
      {items.length === 0
        ? <div className="p-4 text-xs text-dim">{empty ?? 'No entries yet'}</div>
        : <div className="feed max-h-96 overflow-y-auto divide-y divide-border/60">
            {items.map((item, i) => renderItem(item, i))}
          </div>
      }
    </section>
  )
}
