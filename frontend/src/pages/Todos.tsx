import { useEffect, useState } from 'react'

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

  useEffect(() => {
    fetch('/api/todos').then(r => r.json()).then(setTodos).catch(() => {})
  }, [])

  const categories = ['all', ...Array.from(new Set(todos.map(t => t.category))).filter(Boolean).sort()]

  const filtered = filter === 'all' ? todos : todos.filter(t => t.category === filter)
  const open = filtered.filter(t => t.status !== 'done')
  const done = filtered.filter(t => t.status === 'done')

  return (
    <div className="min-h-screen bg-bg text-ink font-sans">
      <div className="max-w-[860px] mx-auto px-8 py-10">

        <div className="mb-8">
          <div className="text-[22px] tracking-tight mb-1">Backlog</div>
          <div className="text-[11px] text-faint font-mono">
            {open.length} open · {done.length} done · edit <span className="text-dim">todos/*.md</span> to add items
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
