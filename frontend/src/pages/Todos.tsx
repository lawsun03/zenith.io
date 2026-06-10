import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

const FONTS_ID = 'todos-grotesk-fonts'
function injectFonts() {
  if (document.getElementById(FONTS_ID)) return
  const link = document.createElement('link')
  link.id = FONTS_ID
  link.rel = 'stylesheet'
  link.href = 'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700;800&family=JetBrains+Mono:wght@400;500&display=swap'
  document.head.appendChild(link)
}

const mono: CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }
const sans: CSSProperties = { fontFamily: "'Space Grotesk', sans-serif" }

const C = {
  bg:    '#070c1a',
  bd:    '#1d2a42',
  bdh:   '#2c3e5c',
  ink:   '#e8f0ff',
  dim:   '#6a85b0',
  faint: '#3d5070',
}

interface Todo {
  id: string
  title: string
  priority: 'high' | 'medium' | 'low'
  status: 'open' | 'in-progress' | 'done'
  category: string
  created: string
  body: string
}

const PRIORITY_DOT: Record<string, string> = {
  high: 'bg-danger',
  medium: 'bg-warn',
  low: 'bg-faint',
}

const STATUS_CLS: Record<string, string> = {
  open: 'text-dim border-border',
  'in-progress': 'text-accent-ink border-accent/40 bg-accent/[0.07]',
  done: 'text-faint border-border line-through',
}

const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  'in-progress': 'In Progress',
  done: 'Done',
}

export function TodosPage() {
  const [todos, setTodos] = useState<Todo[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => { injectFonts() }, [])
  useEffect(() => {
    fetch('/api/todos').then(r => r.json()).then(setTodos).catch(() => {})
  }, [])

  const categories = ['all', ...Array.from(new Set(todos.map(t => t.category))).filter(Boolean).sort()]

  const filtered = filter === 'all' ? todos : todos.filter(t => t.category === filter)
  const open = filtered.filter(t => t.status !== 'done')
  const done = filtered.filter(t => t.status === 'done')

  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.ink }}>
      {/* Nav */}
      <div style={{
        borderBottom: `1px solid ${C.bd}`, padding: '20px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ ...mono, fontSize: 11, letterSpacing: '0.3em', color: C.dim }}>TOPSTEP-BOT</span>
        <div style={{ display: 'flex', gap: 24 }}>
          <a href="/analytics"
            style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}>
            ANALYTICS
          </a>
          <a href="/"
            style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}>
            ← LIVE
          </a>
        </div>
      </div>

      <div style={{ maxWidth: 860, margin: '0 auto', padding: '48px 24px' }}>

        <div style={{ marginBottom: 48 }}>
          <div style={{ ...sans, fontSize: 60, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.02em', marginBottom: 10 }}>
            STRATEGY<br />BACKLOG
          </div>
          <div style={{ ...mono, fontSize: 11, color: C.dim, letterSpacing: '0.15em' }}>
            {open.length} OPEN · {done.length} DONE · EDIT <span style={{ color: C.dim }}>todos/*.md</span> TO ADD ITEMS
          </div>
        </div>

        {/* category filter */}
        <div className="flex gap-3 mb-7 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={`text-[11px] font-mono px-3 py-1 border transition-colors ${
                filter === cat
                  ? 'border-accent/50 bg-accent/10 text-accent-ink'
                  : 'border-border text-faint hover:text-dim hover:border-border-hi'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* open items */}
        {open.length > 0 && (
          <div className="flex flex-col gap-2 mb-6">
            {open.map(todo => (
              <TodoCard
                key={todo.id}
                todo={todo}
                expanded={expanded === todo.id}
                onToggle={() => setExpanded(expanded === todo.id ? null : todo.id)}
              />
            ))}
          </div>
        )}

        {/* done items */}
        {done.length > 0 && (
          <>
            <div className="text-[9px] tracking-[0.12em] uppercase text-faint font-mono mb-2 mt-8">Completed</div>
            <div className="flex flex-col gap-2">
              {done.map(todo => (
                <TodoCard
                  key={todo.id}
                  todo={todo}
                  expanded={expanded === todo.id}
                  onToggle={() => setExpanded(expanded === todo.id ? null : todo.id)}
                />
              ))}
            </div>
          </>
        )}

        {filtered.length === 0 && (
          <div className="text-faint text-sm py-12 text-center">No items</div>
        )}
      </div>
    </div>
  )
}

function TodoCard({ todo, expanded, onToggle }: { todo: Todo; expanded: boolean; onToggle: () => void }) {
  return (
    <div
      className={`bg-panel border border-border overflow-hidden transition-opacity ${todo.status === 'done' ? 'opacity-50' : ''}`}
    >
      <button
        onClick={onToggle}
        className="w-full text-left px-5 py-4 flex items-start gap-3 hover:bg-white/[0.02] transition-colors"
      >
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 mt-[7px] ${PRIORITY_DOT[todo.priority] ?? 'bg-faint'}`} />
        <div className="flex-1 min-w-0">
          <div className={`text-[13px] leading-snug ${todo.status === 'done' ? 'line-through text-faint' : 'text-ink'}`}>
            {todo.title}
          </div>
          <div className="flex items-center gap-2 mt-1.5">
            {todo.category && (
              <span className="text-[9px] font-mono tracking-[0.08em] uppercase text-faint">{todo.category}</span>
            )}
            {todo.created && (
              <span className="text-[9px] font-mono text-faint">{todo.created}</span>
            )}
          </div>
        </div>
        <span className={`text-[10px] font-mono px-2 py-0.5 border shrink-0 ${STATUS_CLS[todo.status] ?? 'text-faint border-border'}`}>
          {STATUS_LABEL[todo.status] ?? todo.status}
        </span>
      </button>
      {expanded && todo.body && (
        <div className="px-5 pb-4 pt-0 pl-[37px]">
          <div className="text-[12px] text-dim leading-relaxed whitespace-pre-wrap border-t border-border pt-3">
            {todo.body}
          </div>
        </div>
      )}
    </div>
  )
}
