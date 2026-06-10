title: Partial-beyond-target guard (stranded position fix)
priority: high
status: done
category: risk
created: 2026-06-08
---
Bug found 2026-06-08: when partial_r (1.5) > target_r (0.72 on MNQ trade), the final target fires first, cancels stop + partial, and orphans partial-size contracts with no protection.

Fix applied: _place_partial_bracket_after_fill now validates partial_price is between entry and target before using the group path. Falls back to plain bracket if partial would be beyond target. Defense-in-depth: _handle_group_fill is_target path now flattens stranded contracts if partial was never filled.
