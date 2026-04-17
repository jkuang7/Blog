# /run_govern - Compatibility-only runner recovery prompt

This prompt is deprecated from the normal ORX-managed execution path.

Canonical flow:
- ORX interprets intake and handoffs
- ORX selects the active issue or packet
- tmux-codex loads the ORX execution packet
- tmux-codex executes one bounded slice
- ORX decides what happens next

Use this prompt only for manual recovery or migration support when a human explicitly needs to repair legacy local runner state.

If this prompt is used:
- keep the scope to repairing local runner bookkeeping only
- do not widen into new implementation work
- do not create or reroute tickets
- prefer restoring alignment with the active ORX/Linear issue

End with:
- `state_repaired=<yes|no>`
- `scope_status=<ok|narrow|split|reseed>`
- `exiting=<yes>`
