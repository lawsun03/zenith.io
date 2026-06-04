# Symbol Switcher — Design Spec
_2026-06-04_

## Goal
Add a symbol pill strip to the dashboard so the user can switch which of the active traded symbols (MGC / MNQ / MES) the chart and strategy debug panel display. Signals, fills, and reconcile feeds remain unfiltered (show all 3 symbols).

## Scope
- Frontend only. No backend changes.
- Only visible when `config.instruments` has 2+ entries (single-symbol mode unchanged).

## Data flow

The bar WebSocket message already carries `payload.instrument`. The `strategy_state` message already carries `payload.instrument`. No server changes needed.

## State

In `App.tsx`:
- Derive `symbols: string[]` from `config?.instruments ?? []`, falling back to `[config?.instrument ?? 'MGC']` when the list is empty.
- `const [activeSymbol, setActiveSymbol] = useState<string>(() => symbols[0] ?? 'MGC')`
- `const activeSymbolRef = useRef(activeSymbol)` — kept in sync via `useEffect` so the `ws.onmessage` closure can read the current value without reconnecting.

Pass `activeSymbolRef` to `useStream`.

## useStream changes

New optional parameter: `activeSymbolRef?: React.MutableRefObject<string>`

Inside `ws.onmessage`:
- `bar` messages: skip `chartCbRef?.current?.onBar?.(...)` when `activeSymbolRef` is set and `msg.payload.instrument !== activeSymbolRef.current`.
- `strategy_state` messages: skip `setStrategyState(...)` when `activeSymbolRef` is set and `msg.payload.instrument !== activeSymbolRef.current`.
- All other message kinds (signal, fill, reconcile, snapshot, reset): no change.

Using a ref (not prop state) avoids needing to tear down and re-create the WebSocket on symbol switch.

## UI

Pill strip placed between `<MetricsGrid>` and `<BarChart>` in `App.tsx`. Only rendered when `symbols.length > 1`.

```
[ MGC ]  [ MNQ ]  [ MES ]
```

- Active: `text-accent border border-accent/70 bg-accent/5`
- Inactive: `text-dim border border-dim/30 hover:text-ink hover:border-dim/60`
- Font: `text-[10px] tracking-widest uppercase px-3 py-0.5`
- Container: `flex gap-2`

No new component file. Inline JSX in `App.tsx`.

## Symbol init timing

`config` loads asynchronously. Until it resolves, `symbols` is derived from the empty/null config. The `useState` initialiser runs once, so `activeSymbol` starts as `'MGC'` if config hasn't loaded yet. Once config loads, the symbols list re-derives for rendering the pill strip — but `activeSymbol` state is not reset (user's selection is preserved). This is correct: if the bot is running MGC the chart has been receiving MGC bars anyway.

## Files changed
- `frontend/src/hooks/useStream.ts` — add `activeSymbolRef` param, filter bar + strategy_state
- `frontend/src/App.tsx` — derive symbols, add state + ref, render pill strip, pass ref to useStream

## Success criteria
- In multi-symbol mode: clicking MNQ pill switches chart to MNQ bars and strategy debug to MNQ state.
- MGC bars no longer appear on the chart while MNQ is selected.
- Signals/fills feeds continue to show all 3 symbols regardless of selection.
- In single-symbol mode: pill strip not rendered, behavior identical to before.
