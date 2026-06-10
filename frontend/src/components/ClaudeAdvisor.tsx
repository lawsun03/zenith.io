import { useState, useRef } from 'react'
import type { CSSProperties } from 'react'

const C = {
  bg:    '#070c1a',
  surf:  '#0d1628',
  bd:    '#1d2a42',
  bdh:   '#2c3e5c',
  ink:   '#e8f0ff',
  dim:   '#6a85b0',
  faint: '#3d5070',
  green: '#6ee7b7',
  red:   '#fca5a5',
  warn:  '#fbbf24',
}

const mono: CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }

type Phase = 'idle' | 'investigating' | 'responding' | 'done' | 'error'

interface ToolEvent {
  name: string
  done: boolean
}

export function ClaudeAdvisor() {
  const [phase, setPhase]       = useState<Phase>('idle')
  const [question, setQuestion] = useState('')
  const [toolLog, setToolLog]   = useState<ToolEvent[]>([])
  const [response, setResponse] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
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

      if (!res.body) { setPhase('error'); setErrorMsg('No response body.'); return }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            let event: Record<string, unknown>
            try { event = JSON.parse(line.slice(6)) } catch { continue }

            if (event.type === 'tool_call') {
              setPhase('investigating')
              setToolLog(prev => [...prev, { name: event.name as string, done: false }])
            } else if (event.type === 'tool_result') {
              setToolLog(prev => prev.map(t => t.name === event.name ? { ...t, done: true } : t))
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

  const sectionHeader = (
    <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div style={{ ...mono, fontSize: 10, letterSpacing: '0.3em', color: C.faint, textTransform: 'uppercase' as const }}>
        ◈ CLAUDE ADVISOR
      </div>
      {phase !== 'idle' && (
        <button
          onClick={handleReset}
          style={{ ...mono, fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase' as const, color: C.faint, border: `1px solid ${C.bd}`, padding: '4px 10px', background: 'none', cursor: 'pointer' }}
          onMouseEnter={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.ink; b.style.borderColor = C.bdh }}
          onMouseLeave={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.faint; b.style.borderColor = C.bd }}
        >
          RESET
        </button>
      )}
    </div>
  )

  return (
    <div>
      {sectionHeader}
      <div style={{ border: `1px solid ${C.bd}` }}>
        {/* Input row */}
        {phase === 'idle' && (
          <div style={{ display: 'flex', borderBottom: `1px solid ${C.bd}` }}>
            <input
              type="text"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAsk()}
              placeholder="e.g. Why is NY AM underperforming? (leave blank for full analysis)"
              style={{
                flex: 1, background: 'transparent', border: 'none',
                ...mono, fontSize: 12, color: C.ink,
                padding: '14px 20px', outline: 'none',
              }}
            />
            <button
              onClick={handleAsk}
              style={{
                ...mono, fontSize: 10, letterSpacing: '0.2em',
                textTransform: 'uppercase' as const,
                color: C.green, borderLeft: `1px solid ${C.bd}`,
                padding: '0 20px', background: 'none', cursor: 'pointer',
              }}
              onMouseEnter={e => ((e.currentTarget as HTMLButtonElement).style.background = 'rgba(110,231,183,0.06)')}
              onMouseLeave={e => ((e.currentTarget as HTMLButtonElement).style.background = 'none')}
            >
              ASK
            </button>
          </div>
        )}

        {/* Tool log */}
        {(phase === 'investigating' || phase === 'responding' || phase === 'done') && toolLog.length > 0 && (
          <div style={{ borderBottom: `1px solid ${C.bd}`, padding: '14px 20px' }}>
            <div style={{ ...mono, fontSize: 9, letterSpacing: '0.25em', color: C.faint, textTransform: 'uppercase' as const, marginBottom: 10 }}>
              {phase === 'investigating' ? 'INVESTIGATING…' : 'ANALYSIS COMPLETE'}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 6 }}>
              {toolLog.map((t, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, ...mono, fontSize: 11 }}>
                  <span style={{ color: t.done ? C.green : C.warn }}>
                    {t.done ? '✓' : '▶'}
                  </span>
                  <span style={{ color: C.ink }}>{t.name}()</span>
                  <span style={{ color: C.faint }}>{t.done ? '· done' : '· running…'}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Response */}
        {(phase === 'responding' || phase === 'done') && response && (
          <div style={{ padding: '20px' }}>
            <div style={{ ...mono, fontSize: 9, letterSpacing: '0.25em', color: C.faint, textTransform: 'uppercase' as const, marginBottom: 14 }}>
              RECOMMENDATION
            </div>
            <pre style={{ ...mono, fontSize: 12, color: C.ink, whiteSpace: 'pre-wrap', lineHeight: 1.7, margin: 0 }}>
              {response}
              {phase === 'responding' && <span style={{ color: C.green }}>▌</span>}
            </pre>
          </div>
        )}

        {/* Done: ask again */}
        {phase === 'done' && (
          <div style={{ borderTop: `1px solid ${C.bd}`, padding: '14px 20px' }}>
            <button
              onClick={handleReset}
              style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', textTransform: 'uppercase' as const, color: C.faint, border: `1px solid ${C.bd}`, padding: '6px 14px', background: 'none', cursor: 'pointer' }}
              onMouseEnter={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.ink; b.style.borderColor = C.bdh }}
              onMouseLeave={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.faint; b.style.borderColor = C.bd }}
            >
              ASK AGAIN
            </button>
          </div>
        )}

        {/* Error */}
        {phase === 'error' && (
          <div style={{ padding: '20px' }}>
            <div style={{ ...mono, fontSize: 9, letterSpacing: '0.25em', color: C.red, textTransform: 'uppercase' as const, marginBottom: 10 }}>
              ERROR
            </div>
            <div style={{ ...mono, fontSize: 12, color: C.red, marginBottom: 14 }}>{errorMsg}</div>
            <button
              onClick={handleReset}
              style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', textTransform: 'uppercase' as const, color: C.faint, border: `1px solid ${C.bd}`, padding: '6px 14px', background: 'none', cursor: 'pointer' }}
            >
              RETRY
            </button>
          </div>
        )}

        {/* Empty prompt in non-idle phases */}
        {(phase === 'investigating' && toolLog.length === 0) && (
          <div style={{ padding: '20px', ...mono, fontSize: 11, color: C.faint }}>
            Connecting…
          </div>
        )}
      </div>
    </div>
  )
}
