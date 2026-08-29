# On the Clock — mock draft room

**Hosted: https://nickwarshak.github.io/FantasyPrep/mockdraft/**

There is also an Artifact copy at
<https://claude.ai/code/artifact/05d0e773-8442-4ee5-9067-d63e0226c6e1>. It is the
same app, but the Artifact viewer blocks sockets, so live online drafting only
works on the hosted copy.

> **Turning on GitHub Pages** — once, in the repo: Settings → Pages → Source
> "Deploy from a branch" → `main` / `/ (root)` → Save. A minute later the URL
> above is live. The repo is already public, so this costs nothing.

A self-contained mock draft room built on `../espn_ppr300_cheatsheet.csv`. It is
deliberately **separate from the rest of this repo** — it shares no code with the
projection pipeline and reads none of its outputs. The only input is the ESPN
cheat sheet.

## Files

| File | |
|---|---|
| `template.html` | the app — edit this |
| `build.py` | inlines the cheat sheet into `index.html` |
| `index.html` | **generated** — do not edit by hand |
| `firebase-config.js` | your Firebase details, for online play; carries the setup steps |
| `firebase-rules.json` | database rules — paste into Firebase, not optional |
| `test_draft.js` | headless tests of the draft engine |

```
python mockdraft/build.py      # rebuild index.html after editing template.html
node   mockdraft/test_draft.js # run the engine tests
```

## Room settings

4–16 teams · snake, 3rd-round reversal, or linear · a pick clock or none · exact
seconds between bot picks · randomness · per-slot roster builder including
SUPERFLEX · keepers.

The lobby blocks a start it cannot honour — the sheet is only 300 deep and holds
just 18 kickers and 22 defenses, so a large field with a deep bench outruns it.

## Keepers

Click any pick on the lobby's draft board and choose a player. The keeper spends
that team's pick in that round, the way a real keeper league works. Keepers go
onto rosters before the first live pick, so they leave the board immediately and
the bots draft around them; the live board shows them tagged `KEPT`. Click a
filled square to change or remove it.

Invalid setups are dropped rather than trusted: a keeper on a team that no longer
exists, or past the last round, is removed when the room changes shape.

## Saved setups

Everything in the lobby — room, seats, roster, keepers, randomness — persists in
the browser. The last setup restores itself on load, and named setups can be
saved, loaded and deleted for more than one league. It is browser-local, so it is
per device and per browser, and every access is guarded because storage can
simply throw (private windows, blocked site data).

Loaded setups are validated, not trusted: team counts are clamped, unknown draft
types and clocks fall back to defaults, and keepers outside the current shape are
dropped.

## Randomness

How far the bots stray from the cheat sheet, from `None` to `Chaos`. Noise widens
by round at every setting, so early picks stay tight and later ones reach; the
setting scales that whole curve. At `None` the room drafts the sheet straight
down — useful for seeing exactly where a player goes at par. `Realistic` is the
default and is what the measured shape below describes.

## In the draft room

- The player list shows **only who is left**. `Show drafted` puts everyone back,
  struck through.
- `Settings` opens the pick clock, the exact seconds between bot picks, and the
  randomness, all changeable mid-draft. Changing the clock re-times a countdown
  already running rather than waiting for the next pick.
- `Pause` banks whatever time was left, so resuming does not hand you a clock
  that has quietly expired.
- **Every square on the board is editable** (offline). Click one and pick:
  - the square **on the clock** — makes that pick;
  - a **past** square — rewrites it, putting the replaced player back on the
    board. Past squares can be replaced but not emptied, since a hole would
    leave that team a player short with no way to fill it;
  - a **future** square — reserves it, spending that pick like a keeper. The
    draft skips it when it arrives.

  A player already on someone's roster is never offered, and `assignPick` refuses
  one anyway so no caller can put one man on two rosters.

## Playing with other people

Three modes, chosen in the lobby.

**Just me** — one seat is yours, bots take the rest.

**Same screen** — give a seat to each person present. Every human seat keeps its
own queue, and when the device changes hands the board and queue are covered by a
hand-off screen until the next drafter says they are ready. Fully reliable, and
it keeps the pick clock.

**Online with friends** — everyone opens the page and drafts live on their own
device. Two transports, picked automatically:

*Firebase (the hosted copy).* Create a room, send the code or the invite link,
and it behaves like a real draft room: picks appear immediately, the clock is
genuinely shared, and you can close the tab and come back. Any seat nobody claims
drafts as a bot. Needs about three minutes of one-time setup — see
`firebase-config.js` for the steps and `firebase-rules.json` for the rules.

*Republishing (the Artifact copy).* A published Artifact cannot open a socket, so
there it falls back to saving the page after each pick: turn-based, untimed, and
everyone needs edit access rather than just the link.

With neither available the online option is disabled rather than left in place
quietly broken. Solo and same-screen need no setup at all.

### How the live room works

The room lives at `/rooms/<code>` and is the only source of truth — nothing is
held on any one client, so no host has to stay online.

| | |
|---|---|
| `meta` | the settings the room was created with, written once |
| `seats` | claimed human seats; anything unclaimed drafts as a bot |
| `picks` | keyed by overall pick number, **create-only** |
| `clock` | who is on the clock and when their time expires |

Picks are keyed by pick number and the rules make them create-only, so two people
picking at the same instant cannot both land: the second transaction sees a value
and aborts, and that client re-renders onto the winner's board. The same rule is
why nobody can rewrite an earlier pick — in a live room only the pick on the
clock is editable, and only by whoever holds that seat.

Countdowns are corrected by Firebase's `serverTimeOffset`, so every client's
clock agrees rather than drifting apart.

One client nominally drives the bot seats; the others stand by a few seconds and
step in if it went quiet, so a closed laptop never stalls a room. A drafter who
disappears gets their clock plus five seconds before another client picks for
them — off the board, since queues are local and cannot be honoured from another
machine.

The `apiKey` and `databaseURL` are public in the page by design; they identify
the project, they do not grant access. `firebase-rules.json` is what actually
controls writes, which is why publishing it is not optional.

## How the bots draft

Each bot scores the board in rank space; lowest score picks. Starting from the
cheat sheet rank:

- **Roster caps are hard.** A capped player is taken only if nothing else is
  worth considering. Caps grow with roster depth so a deep bench doesn't force
  teams into a third quarterback just to fill out.
- **Kickers and defenses are held** until the last couple of rounds.
- **Open starting slots pull players up**, by an amount that grows as the draft
  runs out of room. Once picks remaining equals starting slots open, nothing
  else is considered.
- **Noise widens by round** — early picks track the sheet closely, later ones
  reach. This is what gives the board its texture.

The candidate pool is the top 70 available *plus the best player left at every
position with an open starting slot*. That second part is load-bearing: the best
defense on the sheet is ranked 169th and the best kicker 181st, so a rank window
alone would never surface them in a shallow room and teams would finish with
holes they could not fill.

Measured over 25 twelve-team drafts at the default randomness: round 1 splits
50/50 RB/WR, quarterbacks start moving in round 5 and peak in round 7, tight ends
peak rounds 8–9, and kickers and defenses land almost entirely in the last three
rounds.

## Tests

`test_draft.js` pulls the engine straight out of the built `index.html`, so the
tests exercise the shipped source. It locates script blocks by content rather
than position, so adding one does not silently break the suite.

Coverage: draft-order maths for all three types; that the board tiles every pick
exactly once with no square under the wrong team; lineup assignment; keeper
placement and validation; saved-setup round-trips including corrupted input;
randomness from none to chaos; hand-editing the board (overwrite, reserve, clear,
and the double-assign guard); rebuilding a draft from a live room snapshot,
including the gaps keepers leave in the pick map; and full drafts across ten room
configurations, asserting every roster ends full and legal with no player taken
twice.

Four bugs it caught, all since fixed:

- the bot candidate window was a rank window, so the best defense (169th) and
  kicker (181st) never surfaced in a shallow room and teams finished with holes;
- roster caps summed to fewer players than a deep roster required, forcing teams
  into a third quarterback;
- the board laid cells out by pick order, so every reversed snake round was
  mirrored against its own headers and a team's column zig-zagged;
- `assignPick` would put a player on a second roster if a caller asked it to.
