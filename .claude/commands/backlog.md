# /backlog {project} - Backlog Management

**Purpose**: View and manage the project backlog. Add items, change priorities, activate tasks.

**Your Job**: Quick CRUD operations on the backlog. No planning, no execution.

---

## Arguments

`$ARGUMENTS` format: `{project} [action] [details]`

### Usage Examples

```
/backlog myproject                          # View backlog
/backlog myproject add Fix login timeout    # Add medium priority
/backlog myproject add high Critical bug    # Add high priority
/backlog myproject add low Refactor later   # Add low priority
/backlog myproject bug Users can't logout   # Add bug (auto high)
/backlog myproject promote 3                # Move item 3 up
/backlog myproject demote 2                 # Move item 2 down
/backlog myproject remove 5                 # Remove item 5
/backlog myproject activate 1               # Move to active task
```

---

## Metadata Location

Reads/writes: `~/.claude/projects/{project}.md`

---

## Actions

### View Backlog (no action)

`/backlog {project}`

Load project metadata and display formatted backlog:

```
## Backlog for {project}

### High Priority (2 items)
1. [BUG] Login fails with special chars - Added: 2024-01-15
2. Add rate limiting to API - Added: 2024-01-14

### Medium Priority (3 items)
3. Improve error messages - Added: 2024-01-13
4. Add CSV export feature - Added: 2024-01-12
5. Update documentation - Added: 2024-01-10

### Low Priority (1 item)
6. Refactor auth module - Added: 2024-01-08

---
**Active Task**: {title or "None"}
**Total**: 6 backlog items
```

---

### Add Item

`/backlog {project} add [priority] {description}`

Priority is optional (default: medium):
- `add {description}` → Medium priority
- `add high {description}` → High priority
- `add low {description}` → Low priority

**Action**:
1. Parse description from arguments
2. Add to appropriate priority section
3. Add timestamp
4. Update metadata file

**Confirm**:
```
Added to {priority} priority:
"{description}"

Backlog now has {N} items.
```

---

### Add Bug

`/backlog {project} bug {description}`

Bugs are automatically HIGH priority with [BUG] prefix.

**Action**:
1. Prefix description with "[BUG]"
2. Add to High Priority section
3. Update metadata

**If active task exists**:
```
Added bug to high priority:
"[BUG] {description}"

Note: Active task "{title}" in progress.
Is this blocking? Options:
1. Continue current task
2. Pause and switch to bug (use /plan)
```

---

### Promote Item

`/backlog {project} promote {number}`

Move item to higher priority level.

**Rules**:
- High → (can't promote further)
- Medium → High
- Low → Medium

**Action**:
1. Find item by number (items numbered across all priorities)
2. Move to next priority level
3. Update metadata

**Confirm**:
```
Promoted item {N}:
"{description}"
Low → Medium

Updated backlog:
[show updated backlog]
```

---

### Demote Item

`/backlog {project} demote {number}`

Move item to lower priority level.

**Rules**:
- High → Medium
- Medium → Low
- Low → (can't demote further)

---

### Remove Item

`/backlog {project} remove {number}`

**Action**:
1. Find item by number
2. Confirm removal:
   ```
   Remove from backlog?
   "{description}"

   [y/n]
   ```
3. On confirmation, remove and update metadata

---

### Activate Item

`/backlog {project} activate {number}`

Move backlog item to active task (requires /plan to define steps).

**If no active task**:
```
Activating backlog item:
"{description}"

This item needs step breakdown before execution.
Run `/plan {project}` to define steps.
```

Update metadata:
- Remove from backlog
- Add to Active Task section with status "planning"
- Clear steps (to be defined by /plan)

**If active task exists**:
```
Cannot activate - active task exists:
"{current task title}"

Options:
1. Complete current task first
2. Archive current task to backlog
3. Cancel activation

Use `/plan {project}` to manage active task.
```

---

## Quick Add During Testing

When testing and finding issues, rapid capture:

```
/backlog myproject bug Form validation broken
```

This allows quick capture without interrupting testing flow.

---

## Metadata Updates

### Backlog Section Format

```markdown
## Backlog

### High Priority
- [BUG] Login fails with special chars - Added: 2024-01-15
- Add rate limiting to API - Added: 2024-01-14

### Medium Priority
- Improve error messages - Added: 2024-01-13

### Low Priority
- Refactor auth module - Added: 2024-01-08
```

### On Any Change

Update `last_updated` timestamp in frontmatter.

---

## Error Handling

### Project Not Found
```
Project '{project}' not found.
Run `/plan {project}` to create it.
```

### Invalid Item Number
```
Item {N} not found.
Backlog has {M} items (numbered 1-{M}).
```

### Empty Backlog
```
Backlog is empty.

Add items with:
  /backlog {project} add {description}
  /backlog {project} bug {description}
```

### Can't Promote High Priority
```
Item {N} is already high priority.
Cannot promote further.
```

### Can't Demote Low Priority
```
Item {N} is already low priority.
Cannot demote further.
```

---

## Key Reminders

1. **Quick operations** - This is for fast backlog management
2. **No planning** - Use /plan for step breakdown
3. **No execution** - Use /run for implementation
4. **Always update timestamp** - Keep metadata current
5. **Bugs auto high** - Bug command assumes urgency
6. **One active task** - Can't activate if task exists

---

## Summary

You are the **Backlog Manager**. Your job:

- **View**: Show formatted backlog with priorities
- **Add**: Add items at specified priority
- **Bug**: Add bugs (auto high priority)
- **Promote/Demote**: Change priority levels
- **Remove**: Delete items
- **Activate**: Move to active (requires /plan)

**Core pattern**: Read metadata → Perform action → Update metadata → Confirm

**Philosophy**: Fast, simple CRUD. No planning or execution logic.
