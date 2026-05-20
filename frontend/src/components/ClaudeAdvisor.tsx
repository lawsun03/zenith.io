import { useState, useRef } from 'react'

type Phase = 'idle' | 'investigating' | 'responding' | 'done' | 'error'

interface ToolEvent {
  name: string
  done: boolean
}

export function ClaudeAdvisor() {
  const [phase, setPhase]         = useState<Phase>('idle')
  const [question, setQuestion]   = useState('')
  const [toolLog, setToolLog]     = useState<ToolEvent[]>([])
  const [response, setResponse]   = useState('')
  const [errorMsg, setErrorMsg]   = useState('')
  const abortRef = useRef<AbortController | null>(null)

  async function handleAsk() {
    setPhase('investigating')
    setToolLog([])
    setResponse('')
    setErrorMsg('')

    const abort = new AbortController()
    abortRef.current = abort

    try {
      const res = await fetch('/api/analytics/ask-claude', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim() || null }),
        signal: abort.signal,
      })

      if (!res.body) {
        setPhase('error')
        setErrorMsg('No response body from server.')
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE events are delimited by double newlines
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            let event: Record<string, unknown>
            try {
              event = JSON.parse(line.slice(6))
            } catch {
              continue
            }

            if (event.type === 'tool_call') {
              setPhase('investigating')
              setToolLog(prev => [...prev, { name: event.name as string, done: false }])
            } else if (event.type === 'tool_result') {
              setToolLog(prev =>
                prev.map(t => t.name === event.name ? { ...t, done: true } : t)
              )
            } else if (event.type === 'text_delta') {
              setPhase('responding')
              setResponse(prev => prev + (event.delta as string))
            } else if (event.type === 'done') {
              setPhase('done')
            } else if (event.type === 'error') {
              setPhase('error')
              setErrorMsg(event.message as string)
            }
          }
        }
      }
      // Only set done if we didn't already transition to error via an event
      setPhase(prev => (prev === 'investigating' || prev === 'responding') ? 'done' : prev)
    } catch (err: unknown) {
      if ((err as Error).name === 'AbortError') return
      setPhase('error')
      setErrorMsg(String(err))
    }
  }

  function handleReset() {
    abortRef.current?.abort()
    setPhase('idle')
    setToolLog([])
    setResponse('')
    setErrorMsg('')
  }

  return (
    <div className="bg-panel border border-border">
      {/* Header */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">◈ Claude Advisor</span>
        {phase !== 'idle' && (
          <button
            onClick={handleReset}
            className="text-[9px] tracking-widest uppercase text-dim hover:text-ink border border-border px-2 py-0.5"
          >
            Reset
          </button>
        )}
      </div>

      <div className="p-4 flex flex-col gap-3">
        {/* Input row */}
        {phase === 'idle' && (
          <div className="flex gap-2">
            <input
              type="text"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAsk()}
              placeholder="e.g. Why is NY AM underperforming? (leave blank for full analysis)"
              className="flex-1 bg-bg border border-border text-ink font-mono text-xs px-3 py-2 placeholder:text-dim focus:outline-none focus:border-accent/50"
            />
            <button
              onClick={handleAsk}
              className="text-[10px] tracking-widest uppercase border border-accent/60 text-accent px-4 py-2 hover:bg-accent/10"
            >
              Ask Claude
            </button>
          </div>
        )}

        {/* Investigation log */}
        {(phase === 'investigating' || phase === 'responding' || phase === 'done') && toolLog.length > 0 && (
          <div className="border border-border/50 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-2">
              {phase === 'investigating' ? 'Investigating...' : 'Investigation complete'}
            </div>
            <div className="flex flex-col gap-1">
              {toolLog.map((t, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                  <span className={t.done ? 'text-accent' : 'text-warn animate-pulse'}>▶</span>
                  <span className="text-ink">{t.name}()</span>
                  <span className="text-dim">{t.done ? '· done' : '· running...'}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Streaming response */}
        {(phase === 'responding' || phase === 'done') && response && (
          <div className="border border-border/50 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-2">Recommendation</div>
            <pre className="text-ink text-xs font-mono whitespace-pre-wrap leading-relaxed">
              {response}
              {phase === 'responding' && <span className="animate-pulse">▌</span>}
            </pre>
          </div>
        )}

        {/* Done: ask again */}
        {phase === 'done' && (
          <button
            onClick={handleReset}
            className="self-start text-[9px] tracking-widest uppercase border border-border text-dim px-3 py-1 hover:text-ink hover:border-ink"
          >
            Ask Again
          </button>
        )}

        {/* Error */}
        {phase === 'error' && (
          <div className="border border-danger/40 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-danger uppercase mb-1">Error</div>
            <p className="text-danger text-xs font-mono">{errorMsg}</p>
            <button
              onClick={handleReset}
              className="mt-2 text-[9px] tracking-widest uppercase border border-danger/40 text-danger/70 px-3 py-1 hover:bg-danger/10"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
