# Accessibility and presentation review (P1 interface)

Scope: the five P1 screens (Inventory, Receive supplies, Issue supplies, Counts, History) in five states: desktop
light, desktop dark, phone (375 px) light, phone dark, and 640 CSS px reflow (not browser zoom). Tested in headless Chromium in the
cloud container. Not yet tested with VoiceOver or on a physical phone (see the checklist below).

## Method and exact coverage

| Script | What it verifies | Result |
|---|---|---|
| `e2e/a11y-audit.mjs` | axe-core 4.14.0 (pinned devDependency) on each screen in the five states; horizontal page scroll. Exits 1 on any violation or scroll. | 25 checks, 0 findings |
| `e2e/keyboard-check.mjs` | Keyboard-only (Tab, Enter, Space) workflows: Inventory expand/collapse; History expand/collapse; Receive error focus then a receipt; Issue error focus then an issue; Counts start, error focus on a bad count, a discrepancy submit, and cancel with confirmation; a dropped receipt with focus moved to the pending banner and Retry from the keyboard. Each focused control must show a nonzero outline. Exits 1 on failure. | 24 assertions, 0 failures |
| `e2e/browser-walk.mjs`, `e2e/recovery-cases.mjs` | Regression of behaviour (not accessibility). Re-run after the layout change. | Pass on fresh databases |

The keyboard check does not cover: screen-reader announcements, the Clinic and Demo actor selects, the Counts area
selector, the History and Issue detail tables beyond their toggles, or keyboard use of every select option.
axe does not test focus order or announcements either. "No automated violations" is the claim, not "accessible".

## Findings and fixes (this pass and the previous one)

| Finding | Fix |
|---|---|
| Receive line inputs and selects had no accessible names (axe, critical) | Each control has an aria-label such as "Lot for line 1" |
| Empty table header | Hidden text "Remove" (`sr-only`) |
| Primary button text failed contrast in dark mode | `--on-accent` token: dark text on the dark accent |
| Expandable rows were clickable table rows, not keyboard reachable | Each row toggle is a real button with `aria-expanded` |
| No visible focus indicator | `:focus-visible` ring (3 px, accent colour) in both themes |
| Page scrolled sideways at 375 px | Wide tables scroll inside their panel; hidden labels no longer widen the page |
| Receive lines table was cramped on phones; Unit and Quantity sat off-screen | Below 640 CSS px each line is a card: Product on its own row, Lot and Expiration, then Unit and Quantity side by side. Headers stay in the accessibility tree, visually hidden |
| Validation errors did not move focus | Receive, Issue and Counts move focus to the first invalid field |
| A pending (unknown-outcome) write was easy to miss | Focus moves to the pending banner, which has a keyboard Retry |

## Results

- axe-core: no violations on the five screens in each of the five states (25 checks). The audit fails if this
  changes. A negative control (an image with no alt text injected into every page) made all 25 checks fail.
- Keyboard: all 24 assertions pass on a fresh database; every checked focused control has a nonzero outline.
- No horizontal page scroll at 640, 375 or 320 CSS px, in light or dark.
- Walkthrough and 13 recovery checks pass on fresh databases after the layout change.

## Remaining items

- VoiceOver on the Mac has not been run. Use the checklist below and record results in this file.
- No physical-phone test. The phone layout was checked at emulated widths only.
- Motion and reduced-motion preferences were not reviewed (the interface has no animations).

## VoiceOver checklist (Mac, Safari or Chrome, Cmd+F5 to turn VoiceOver on)

1. Inventory: Tab to a product toggle; VoiceOver says it is a button and announces expanded or collapsed.
2. Receive: each line control announces its label ("Lot for line 1", "Quantity for line 1", and so on).
3. Receive error: submit with an empty lot; VoiceOver announces the error and focus lands on the Lot field.
4. Receive pending: with the first request dropped (or a simulated 503), the pending banner is announced as a status
   and receives focus; Retry is reachable by Tab.
5. Counts: a row's count input is labelled with its SKU and lot.
6. History: a transaction toggle announces expanded or collapsed, and the movements table has headers.
7. Phone layout, 200% zoom in Safari: everything reads in order, with no lost controls.

Record the outcome of each step here (date, browser, result, anything found).

## Mac results (recorded 2026-10-19)

| Check | Result | Browser and date | Notes |
|---|---|---|---|
| Frontend: `npm ci`, typecheck, 4 unit tests, production build | Pass | Mac, 2026-10-19 | Pinned `axe-core` installed with the lockfile |
| Fixed-date app on a fresh database: inventory, receiving, issuing, counts, history | Pass | Mac, 2026-10-19 | Reported by reviewer |
| `e2e/keyboard-check.mjs` | Pass, 24 of 24 assertions | Mac, 2026-10-19 | Fresh database |
| `e2e/a11y-audit.mjs` | Pass, 25 checks, 0 findings, exit 0 | Mac, 2026-10-19 | Five states: desktop light and dark, phone light and dark, 640 px reflow |
| VoiceOver (seven-step checklist above) | Pass, as reported by reviewer | Safari, Mac, 2026-10-19 | Per-step notes not yet recorded: add them to the table |
| Actual 200% browser zoom (receiving, errors, counts, history) | Pass, as reported by reviewer | Mac browser, 2026-10-19 | The automated 640 px case is reflow, not zoom |
| Physical phone | Not verified | n/a | Remains a documented limitation |

Note on the automated `reflow640-light` label: it is a 640 CSS px viewport (reflow). Real 200% zoom is the manual check
above, not the audit.

