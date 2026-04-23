# DOGFOOD — chess-tui

_Session: 2026-04-23T10:12:46, driver: pty, duration: 8.0 min_

**PASS** — ran for 4.7m, captured 64 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found 1 UX note(s). Game reached 11 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`. 1 coverage note(s) — see Coverage section.

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
- **[U1] state() feedback is coarse**
  - Only 11 unique states over 334 samples (ratio 0.03). The driver interface works but reveals little per tick.

## Coverage

- Driver backend: `pty`
- Keys pressed: 1984 (unique: 40)
- State samples: 334 (unique: 11)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=216.7, B=15.1, C=48.0
- Snapshots: `/tmp/tui-dogfood-20260423-100733/reports/snaps/chess-tui-20260423-100734`

Unique keys exercised: -, /, 2, 3, 5, :, ;, ?, H, R, ], backspace, c, ctrl+l, delete, down, enter, escape, f1, f2, h, home, k, l, left, m, n, p, page_down, question_mark, r, right, shift+slash, shift+tab, space, up, v, w, x, z

### Coverage notes

- **[CN1] Phase B exited early due to saturation**
  - State hash unchanged for 10 consecutive samples during the stress probe; remaining keys skipped.

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.3 | 0.0 | `chess-tui-20260423-100734/milestones/first_input.txt` | key=right |
