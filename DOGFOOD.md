# DOGFOOD — chess

_Session: 2026-04-23T14:31:03, driver: pty, duration: 1.5 min_

**PASS** — ran for 1.2m, captured 16 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found no findings worth flagging. Game reached 76 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`.

## Findings

### Blockers

_None._

### Majors

_None._

### Minors

_None._

### Nits

_None._

### UX (feel-better-ifs)

_None._

## Coverage

- Driver backend: `pty`
- Keys pressed: 607 (unique: 58)
- State samples: 116 (unique: 76)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=40.5, B=21.1, C=9.1
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/chess-20260423-142951`

Unique keys exercised: +, ,, -, ., /, 0, 1, 2, 3, 4, 5, :, ;, =, ?, H, R, [, ], a, b, backspace, c, ctrl+l, d, delete, down, end, enter, escape, f1, f2, h, home, j, k, l, left, m, n ...

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.3 | 0.0 | `chess-20260423-142951/milestones/first_input.txt` | key=right |
