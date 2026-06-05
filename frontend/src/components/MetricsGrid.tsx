import type { StatusPayload } from '../types'
import { MetricCard } from './MetricCard'
import { fmtMoney } from '../utils/format'

interface Props { status: StatusPayload | null }

export function MetricsGrid({ status }: Props) {
  if (!status) {
    return <div className="bg-panel border border-border rounded-[10px] p-5 text-faint text-sm">Awaiting state…</div>
  }

  const { equity, limits, account, lockout } = status
  const soft = Number(account.soft_buffer)
  const bufMll = Number(limits.buffer_to_mll)
  const bufDll = Number(limits.buffer_to_dll)
  const pnl    = Number(limits.daily_pnl)

  const mllTone = bufMll <= soft ? 'bad' : bufMll <= soft * 2 ? 'warn' : 'good'
  const dllTone = bufDll <= soft ? 'bad' : bufDll <= soft * 2 ? 'warn' : 'good'
  const pnlTone = pnl > 0 ? 'good' : pnl < 0 ? 'bad' : 'neutral'

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-[11px]">
      <MetricCard
        label="Buffer to MLL"
        primary={fmtMoney(limits.buffer_to_mll)}
        secondary={`Floor ${fmtMoney(limits.mll_floor)} · ${limits.mll_locked_at_starting_balance ? 'LOCKED' : 'trailing'}`}
        tone={mllTone}
        pulse={!lockout}
      />
      <MetricCard
        label="Buffer to DLL"
        primary={fmtMoney(limits.buffer_to_dll)}
        secondary={`Limit ${fmtMoney(account.daily_loss_limit)}`}
        tone={dllTone}
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
