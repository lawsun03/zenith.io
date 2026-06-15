import { useState, useCallback } from 'react'

interface ConfirmOptions {
  title?: string
  confirmLabel?: string
  variant?: 'danger' | 'warn' | 'accent'
}

interface ConfirmState extends ConfirmOptions {
  message: string
  resolve: (ok: boolean) => void
}

export function useConfirm() {
  const [state, setState] = useState<ConfirmState | null>(null)

  const confirm = useCallback((message: string, opts: ConfirmOptions = {}): Promise<boolean> =>
    new Promise(resolve => setState({ message, resolve, ...opts }))
  , [])

  function accept() { state?.resolve(true);  setState(null) }
  function cancel() { state?.resolve(false); setState(null) }

  const variant = state?.variant ?? 'danger'
  const colorMap = {
    danger: 'border-danger text-danger bg-danger/10 hover:bg-danger/20',
    warn:   'border-warn text-warn bg-warn/10 hover:bg-warn/20',
    accent: 'border-accent text-accent bg-accent/10 hover:bg-accent/20',
  }

  const modal = state ? (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={cancel} />
      <div className="relative z-10 bg-panel border border-border w-[360px] p-6 shadow-2xl animate-fade-up">
        {state.title && (
          <div className="text-[9px] tracking-[0.3em] text-dim uppercase mb-3">{state.title}</div>
        )}
        <p className="text-[13px] text-ink leading-relaxed mb-6">{state.message}</p>
        <div className="flex gap-3">
          <button
            autoFocus
            onClick={accept}
            className={`flex-1 border text-[11px] tracking-widest uppercase px-4 py-2 ${colorMap[variant]}`}
          >
            {state.confirmLabel ?? 'Confirm'}
          </button>
          <button
            onClick={cancel}
            className="flex-1 border border-border text-dim text-[11px] tracking-widest uppercase px-4 py-2 hover:text-ink hover:border-ink"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  ) : null

  return { confirm, modal }
}
