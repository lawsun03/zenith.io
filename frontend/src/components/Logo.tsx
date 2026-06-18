interface Props {
  size?: number
}

/** Zenith mark: equity curve cresting at its peak. */
export function Logo({ size = 18 }: Props) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-label="Zenith">
      <polyline
        points="3,23 8,16 12,18.5 18,7 24,12"
        fill="none" stroke="rgb(var(--c-dim))" strokeWidth="2.2"
        strokeLinecap="square" strokeLinejoin="miter"
      />
      <polyline
        points="12,18.5 18,7"
        fill="none" stroke="rgb(var(--c-accent))" strokeWidth="2.2" strokeLinecap="square"
      />
      <circle cx="18" cy="7" r="2.6" fill="rgb(var(--c-accent))" />
    </svg>
  )
}
