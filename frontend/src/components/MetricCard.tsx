type Tone = 'neutral' | 'good' | 'warn' | 'bad'

const TONE_CLS: Record<Tone, string> = {
  neutral: 'text-ink',
  good:    'text-accent',
  warn:    'text-warn',
  bad:     'text-danger',
}

interface Props {
  label: string
  primary: string
  secondary?: string
  tone?: Tone
  pulse?: boolean
}

export function MetricCard({ label, primary, secondary, tone = 'neutral', pulse = true }: Props) {
  return (
    <div className="bg-panel p-5 flex flex-col gap-2">
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase">{label}</div>
      <div className={`font-display text-3xl tabular-nums ${TONE_CLS[tone]} ${pulse ? 'animate-pulse-soft' : ''}`}>
        {primary}
      </div>
      {secondary && <div className="text-xs text-dim">{secondary}</div>}
    </div>
  )
}
