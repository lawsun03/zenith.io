import type { StatusPayload } from '../types'
import { MetricCard } from './MetricCard'
import { fmtMoney } from '../utils/format'

interface Props { status: StatusPayload | null }

export function MetricsGrid({ status }: Props) {
  if (!status) {
    return <div className="bg-panel border border-border p-5 text-faint text-sm">Awaiting state…</div>
  }

  const { equity, limits, account, lockout, daily_trades: dt } = status
  const pnl = Number(limits.daily_pnl)
  const pnlTone = pnl > 0 ? 'good' : pnl < 0 ? 'bad' : 'neutral'

  const winRate = dt?.win_rate != null ? `${Math.round(dt.win_rate * 100)}%` : '—'
  const winRateTone = dt == null || dt.trades === 0 ? 'neutral'
    : dt.win_rate != null && dt.win_rate >= 0.5 ? 'good' : 'bad'
  const wlSecondary = dt && dt.trades > 0 ? `${dt.wins}W / ${dt.losses}L · ${dt.trades} trades` : 'no trades yet'

  const avgPnl = dt?.avg_pnl != null ? fmtMoney(dt.avg_pnl, { signed: true }) : '—'
  const avgTone = dt?.avg_pnl == null ? 'neutral' : dt.avg_pnl > 0 ? 'good' : 'bad'

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-[11px]">
      <MetricCard
        label="Win % Today"
        primary={winRate}
        secondary={wlSecondary}
        tone={winRateTone}
        pulse={!lockout}
      />
      <MetricCard
        label="Avg Trade"
        primary={avgPnl}
        secondary={dt && dt.trades > 0 ? `${dt.trades} closed trades today` : 'no trades yet'}
        tone={avgTone}
        pulse={!lockout}
      />
      <MetricCard
        label="Daily P&L"
        primary={fmtMoney(limits.daily_pnl, { signed: true })}
        secondary={`Realized ${fmtMoney(equity.realized_balance)}`}
        tone={pnlTone}
        pulse={!lockout}
      />
      <MetricCard
        label="Open"
        primary={`${Math.abs(limits.open_contracts)}/${account.max_contracts}`}
        secondary={`Equity ${fmtMoney(equity.current)} · HW ${fmtMoney(equity.high_water)}`}
        tone={Math.abs(limits.open_contracts) === account.max_contracts ? 'warn' : 'neutral'}
        pulse={false}
      />
    </div>
  )
}
