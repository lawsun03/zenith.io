interface Props {
  lockout: { code: string; message: string } | null
}

export function LockoutBanner({ lockout }: Props) {
  if (!lockout) return null
  return (
    <div className="hatch border-y border-danger/40 px-6 py-3 flex items-baseline gap-4">
      <span className="text-danger font-bold tracking-widest text-sm">LOCKED</span>
      <span className="text-xs uppercase tracking-wider text-danger/80">{lockout.code}</span>
      <span className="text-sm text-ink/80">{lockout.message}</span>
    </div>
  )
}
