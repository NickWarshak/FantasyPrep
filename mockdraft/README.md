# On the Clock — mock draft room

**Live: https://claude.ai/code/artifact/05d0e773-8442-4ee5-9067-d63e0226c6e1**

A self-contained mock draft room built on `../espn_ppr300_cheatsheet.csv`. It is
deliberately **separate from the rest of this repo** — it shares no code with the
projection pipeline and reads none of its outputs. The only input is the ESPN
cheat sheet.

It reproduces the mechanics of an online mock draft (lobby settings, snake board,
pick clock, queue, autopick, bot opponents) as its own thing. No other product's
name, branding, or assets are used.

## Files

| File | |
|---|---|
| `template.html` | the app — edit this |
| `build.py` | inlines the cheat sheet into `index.html` |
| `index.html` | **generated** — do not edit by hand |
| `test_draft.js` | headless tests of the draft engine |

```
python mockdraft/build.py      # rebuild index.html after editing template.html
node   mockdraft/test_draft.js # run the engine tests
```

The app ships as one file with the player pool baked in, because it runs as a
published Artifact where runtime fetches are blocked.

## Room settings

4-16 teams - snake, 3rd-round reversal, or linear - 15/30/60/90s or no clock -
per-slot roster builder including SUPERFLEX - three bot speeds - keepers.

The lobby blocks a start it cannot honour - the sheet is only 300 deep and holds
just 18 kickers and 22 defenses, so a large field with a deep bench can outrun it.

## Keepers

Click any pick on the lobby's draft board and choose a player. The keeper spends
that team's pick in that round, the way a real keeper league works. Keepers go
onto rosters before the first live pick, so they leave the board immediately and
the bots draft around them; the live board shows them tagged `KEPT` in the round
they cost. Click a filled cell to change or remove it.

Invalid setups are dropped rather than trusted: a keeper on a team that no longer
exists, or past the last round, is removed when the room changes shape.

## Saved setups

Everything in the lobby -- room, seats, roster, keepers, randomness -- persists in
the browser. The last setup restores itself on load, and named setups can be
saved, loaded and deleted for more than one league. It is browser-local storage,
so it is per device and per browser, and every access is guarded because storage
can simply throw (private windows, blocked site data).

Loaded setups are validated, not trusted: team counts are clamped, unknown draft
types and clocks fall back to defaults, and keepers outside the current shape are
dropped.

## Randomness

How far the bots stray from the cheat sheet, from `None` to `Chaos`. Noise widens
by round at every setting, so early picks stay tight and later ones reach; the
setting scales that whole curve. At `None` the room drafts the sheet straight
down -- useful for seeing exactly where a player goes at par. `Realistic` is the
default and is what the measured draft shape below describes.

## Playing with other people

Three modes, chosen in the lobby.

**Just me** - one seat is yours, bots take the rest.

**Same screen** - give a seat to each person in the room. Every human seat keeps
its own queue, and when the device changes hands the board and queue are covered
by a hand-off screen until the next drafter says they're ready. Fully reliable,
and it keeps the pick clock.

**Online with friends** - everyone opens the artifact link and claims a seat on
their own device. Read the constraints before relying on it:

- There is **no shared database available to this page**. The page itself is the
  record: each human turn rewrites this document with the draft embedded and
  republishes it, and every open view reloads to that new version.
- So play is **turn-based, not live**, and **untimed** - there is no way to run a
  trustworthy shared countdown across devices, so the clock is switched off.
- Friends need **edit access, not just the link**. A view-only viewer gets
  `not_writer` and cannot pick; the room tells them so rather than failing quietly.
- Seat ownership is per device and on the honour system (there is no viewer
  identity here). Each person picks their seat once; it is remembered locally.
- Bots between two people are resolved by whoever picked last and saved with the
  same write, so the draft is one save per human turn rather than one per pick.
- If two people somehow pick at once, one save wins and the other view reloads to
  the winner's board. Only one seat is ever on the clock, so this should be rare.

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

Measured over 25 twelve-team drafts, the resulting shape: round 1 splits 50/50
RB/WR, quarterbacks start moving in round 5 and peak in round 7, tight ends peak
rounds 8–9, and kickers and defenses land almost entirely in the last three
rounds.

## Tests

`test_draft.js` pulls the engine straight out of the built `index.html`, so the
tests exercise the shipped source. It covers draft-order maths for all three
types, lineup assignment (best player takes the flex, not the earliest pick),
queue and autopick behaviour, and full drafts across six room configurations,
asserting every roster ends full and legal with no player taken twice.

Two bugs it caught, both since fixed: the rank-window problem above, and roster
caps that summed to fewer players than a deep roster required.
