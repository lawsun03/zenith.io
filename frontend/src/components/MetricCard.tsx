type Tone = 'neutral' | 'good' | 'warn' | 'bad'

const VALUE_CLS: Record<Tone, string> = {
  neutral: 'text-ink',
  good:    'text-good',
  warn:    'text-warn',
  bad:     'text-danger',
}

const DOT_CLS: Record<Tone, string> = {
  neutral: 'bg-faint',
  good:    'bg-good',
  warn:    'bg-warn',
  bad:     'bg-danger',
}

interface Props {
  label: string
  primary: string
  secondary?: string
  tone?: Tone
  // Accepted for call-site compatibility; intentionally unused (the values are
  // held steady — motion on the main numbers hurts legibility).
  pulse?: boolean
}

export function MetricCard({ label, primary, secondary, tone = 'neutral' }: Props) {
  return (
    <div className="bg-panel backdrop-blur-md border border-border rounded-[10px] px-[18px] py-4 hover:border-border-hi transition-colors">
      <div className="flex items-center gap-[7px] mb-[9px]">
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${DOT_CLS[tone]}`} />
        <span className="text-[9px] tracking-[0.1em] text-faint uppercase font-mono">{label}</span>
      </div>
      <div className={`text-2xl font-medium leading-none tabular-nums ${VALUE_CLS[tone]}`}>
        {primary}
      </div>
      {secondary && <div className="text-[10px] text-faint mt-[7px] font-mono">{secondary}</div>}
    </div>
  )
}
