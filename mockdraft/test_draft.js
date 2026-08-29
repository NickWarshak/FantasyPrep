/* Headless check of the draft engine.
 *
 * Pulls the logic straight out of index.html so the tests exercise the same
 * source the page ships, stubs the DOM and capability calls the engine makes,
 * and runs full drafts across a spread of room settings looking for invariant
 * breaks.
 *
 *     node mockdraft/test_draft.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
// Only bare <script> blocks; the room-state blob carries attributes.
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
// Find blocks by what is in them rather than by position, so inserting a new
// one (the network transport did exactly that) doesn't silently break the suite.
const block = (needle) => {
  const hit = scripts.find(b => b.includes(needle));
  if (!hit) throw new Error('no script block containing ' + needle);
  return hit;
};
// The engine, plus the transport it now calls into. Rendering and DOM wiring
// stay out and are stubbed below.
const engine = [block('function teamAt('), block('function makePick('), block('const Net =')]
  .join('\n');
// The saved-setup helpers live inside the events block; take just the pure part
// (up to the first function that touches the DOM) so they are tested from the
// shipped source rather than a copy.
const events = block('const CFG_KEY');
const persistence = events.slice(events.indexOf('const CFG_KEY'),
                                 events.indexOf('function renderPresets()'));

const stub = `
  function renderAll(){} function renderClockbar(){} function renderClock(){}
  function renderPool(){} function renderRail(){} function renderRecap(){}
  function renderRoomBar(){} function buildFilters(){} function syncHideButton(){}
  const __els = {};
  const document = {
    getElementById: (id) => (__els[id] = __els[id] || { hidden: false, textContent: '', value: '',
      classList: { remove(){}, add(){}, toggle(){} } }),
    querySelector: () => null, querySelectorAll: () => [], addEventListener(){},
    createElement: () => ({ set src(v) {}, set onload(v) {}, set onerror(v) {} }),
    head: { appendChild(){} },
    documentElement: { outerHTML: '<html><body><script id="room-state" type="application/json">null<\/script></body></html>' },
  };
  const __store = {};
  const localStorage = { getItem: k => (k in __store ? __store[k] : null),
                         setItem: (k, v) => { __store[k] = String(v); } };
  function setInterval(){ return 1; } function clearInterval(){}
  function setTimeout(){ return 1; } function clearTimeout(){}
  const window = {}; const location = { href: 'http://test/', search: '' };
`;

const api = new Function(stub + engine + persistence + `
  return { get S(){return S}, set S(v){S=v}, get St(){return St}, set St(v){St=v},
           get MySeat(){return MySeat}, set MySeat(v){MySeat=v},
           teamAt, totalPicks, overallFor, lineup, needs, counts, available,
           botChoice, autoChoice, startDraft, makePick, applyPick, rounds,
           normalizeSeats, isBot, isLocal, teamName, keeperProblems, placeKeepers,
           newState, roomState, restoreRoom, runBotsUntilHuman, freshSettings,
           assignPick, pickAt, boardEditable, skipFilled, Net, fbConfigured,
           onRoomData, clientId, makeCode, myBoardRows, HAS_MINE, rosterCaps,
           espnAhead, picksUntilMyTurn, SOURCES,
           configBlob, applyConfig, readPresets, writePresets, saveConfig,
           SLOTS, STANDARD, P, __els,
           __claimSeat: (n) => localStorage.setItem('otc-seat', String(n)) };
`)();

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures++; console.log('  FAIL  ' + name + (detail ? '  — ' + detail : '')); }
  else console.log('  ok    ' + name);
}
const byName = {};
api.P.forEach(p => { byName[p.name] = p; });
const gib0 = byName['Jahmyr Gibbs'].id;
const ids = names => names.map(n => {
  if (!byName[n]) throw new Error('no such player: ' + n);
  return byName[n].id;
});
function seats(teams, spec) {          // spec: {2:'me', 5:'friend'}, rest bots
  const s = Array(teams).fill('bot');
  Object.keys(spec).forEach(k => { s[+k] = spec[k]; });
  return s;
}
function settings(over) {
  return Object.assign(api.freshSettings(), { teams: 12, type: 'snake', clock: 30, pace: 0 }, over);
}

/* ── Draft order ────────────────────────────────────────────────────────── */
console.log('\ndraft order');
api.S = settings({ teams: 4, type: 'snake', seats: seats(4, { 0: 'me' }) });
const snake = [...Array(12).keys()].map(api.teamAt).join(' ');
check('snake reverses each round', snake === '0 1 2 3 3 2 1 0 0 1 2 3', snake);

api.S.type = 'linear';
check('linear repeats the order',
  [...Array(12).keys()].map(api.teamAt).join(' ') === '0 1 2 3 0 1 2 3 0 1 2 3');

api.S.type = '3rr';
const rr = [...Array(16).keys()].map(api.teamAt).join(' ');
check('3rd round reversal flips from round 3', rr === '0 1 2 3 3 2 1 0 3 2 1 0 0 1 2 3', rr);

for (const type of ['snake', '3rr', 'linear']) {
  api.S.type = type;
  let ok = true;
  for (let t = 0; t < 4; t++) {
    for (let r = 1; r <= 4; r++) {
      const ov = api.overallFor(t, r);
      if (api.teamAt(ov) !== t || Math.floor(ov / 4) !== r - 1) ok = false;
    }
  }
  check('overallFor inverts teamAt (' + type + ')', ok);
}

api.S.type = 'snake';
for (const t of [4, 10, 12, 14]) {
  api.S.teams = t;
  const perRound = {};
  for (let i = 0; i < t * 6; i++) {
    const r = Math.floor(i / t);
    (perRound[r] = perRound[r] || new Set()).add(api.teamAt(i));
  }
  check(t + '-team snake: every team picks once per round',
    Object.values(perRound).every(s => s.size === t));
}

/* ── Board layout ───────────────────────────────────────────────────────────
   Every cell of the rendered board is overallFor(team, round). For the board to
   be right that has to tile the draft exactly: every pick once, no gaps, and no
   cell filed under the wrong team — which is what went wrong in reversed snake
   rounds, where the row was mirrored against its own headers. */
console.log('\nboard layout');
for (const [teams, type] of [[12, 'snake'], [10, '3rr'], [14, 'linear'], [8, 'snake']]) {
  api.S = settings({ teams, type, seats: seats(teams, { 0: 'me' }) });
  api.S.rounds = api.rounds();
  const cells = new Set();
  let mismatched = 0;
  for (let r = 1; r <= api.S.rounds; r++) {
    for (let t = 0; t < teams; t++) {
      const ov = api.overallFor(t, r);
      cells.add(ov);
      if (api.teamAt(ov) !== t) mismatched++;
    }
  }
  check(teams + '-team ' + type + ': the board tiles every pick exactly once',
    cells.size === api.totalPicks() && Math.min(...cells) === 0 &&
    Math.max(...cells) === api.totalPicks() - 1, 'cells=' + cells.size + '/' + api.totalPicks());
  check(teams + '-team ' + type + ': every cell sits under its own team',
    mismatched === 0, mismatched + ' mismatched');
}

/* ── Randomness ─────────────────────────────────────────────────────────── */
console.log('\nrandomness');
function roundOneRanks(randomness) {
  api.S = settings({ teams: 12, seats: seats(12, { 11: 'me' }), randomness });
  api.S.rounds = api.rounds();
  api.startDraft();
  const out = [];
  for (let i = 0; i < 12; i++) {
    const t = api.teamAt(api.St.onClock);
    const pick = api.botChoice(t, false);
    out.push(pick.rank);
    api.makePick(pick.id);
  }
  return out;
}
check('zero randomness drafts the sheet straight down',
  roundOneRanks(0).join(',') === '1,2,3,4,5,6,7,8,9,10,11,12', roundOneRanks(0).join(','));

function drift(randomness, trials) {
  let total = 0;
  for (let k = 0; k < trials; k++) {
    roundOneRanks(randomness).forEach((r, i) => { total += Math.abs(r - (i + 1)); });
  }
  return total / trials;
}
const tame = drift(0.35, 12), wild = drift(2.4, 12);
check('more randomness moves round 1 further off the sheet', wild > tame * 1.5,
  'chalk ' + tame.toFixed(1) + ' vs chaos ' + wild.toFixed(1));
check('even the wildest setting keeps round 1 roughly sane', wild < 70,
  'mean displacement ' + wild.toFixed(1));

/* ── Saved setups ───────────────────────────────────────────────────────── */
console.log('\nsaved setups');
api.S = settings({ teams: 10, type: '3rr', clock: 60, randomness: 1.6, mode: 'hotseat',
                   seats: seats(10, { 2: 'me', 5: 'friend' }),
                   keepers: [{ team: 2, id: gib0, round: 4 }] });
api.S.rounds = api.rounds();
const blob = JSON.parse(JSON.stringify(api.configBlob()));
api.S = settings({});
api.applyConfig(blob);
check('a saved setup restores the room', api.S.teams === 10 && api.S.type === '3rr' &&
  api.S.clock === 60 && api.S.randomness === 1.6 && api.S.mode === 'hotseat');
check('a saved setup restores the seats', (api.S.seats || []).join(',') === blob.seats.join(','));
check('a saved setup restores the keepers',
  api.S.keepers.length === 1 && api.S.keepers[0].team === 2 && api.S.keepers[0].round === 4);

api.applyConfig({ teams: 4, keepers: [{ team: 9, id: gib0, round: 2 }] });
check('keepers for teams that no longer exist are dropped', api.S.keepers.length === 0);
api.applyConfig({ teams: 12, keepers: [{ team: 0, id: gib0, round: 9 }],
                  slots: { QB: 1, RB: 1, WR: 1, TE: 0, FLEX: 0, SFLEX: 0, K: 0, DST: 0, BN: 0 } });
check('keepers past the last round are dropped', api.S.keepers.length === 0, api.S.keepers.length);
api.applyConfig({ teams: 999, type: 'nonsense', clock: 12345, randomness: -5, slots: 'junk' });
check('a corrupted setup is clamped rather than trusted',
  api.S.teams === 16 && api.S.type === 'snake' && api.S.clock === 30 && api.S.randomness === 0,
  [api.S.teams, api.S.type, api.S.clock, api.S.randomness].join(' '));
check('a corrupted setup still leaves a draftable roster', api.rounds() > 0);

/* ── Seats ──────────────────────────────────────────────────────────────── */
console.log('\nseats');
api.S = settings({ teams: 6, mode: 'solo', seats: seats(6, { 1: 'me', 3: 'friend' }) });
api.normalizeSeats();
check('solo has no friend seats', api.S.seats.filter(s => s === 'friend').length === 0,
  api.S.seats.join(','));
check('solo keeps exactly one seat for this device',
  api.S.seats.filter(s => s === 'me').length === 1 && api.MySeat === 1, api.S.seats.join(','));

api.S = settings({ teams: 6, mode: 'hotseat', seats: seats(6, { 0: 'me', 2: 'me', 4: 'friend' }) });
api.normalizeSeats();
check('a second "me" seat becomes a friend seat',
  api.S.seats.filter(s => s === 'me').length === 1 && api.S.seats[2] === 'friend', api.S.seats.join(','));
check('same-screen plays every human seat',
  api.isLocal(0) && api.isLocal(2) && api.isLocal(4) && !api.isLocal(1));

api.S = settings({ teams: 6, mode: 'online', seats: seats(6, { 0: 'me', 2: 'friend' }) });
api.normalizeSeats();
check('online plays only its own seat',
  api.isLocal(0) && !api.isLocal(2) && !api.isLocal(1));

/* ── Lineup assignment ──────────────────────────────────────────────────── */
console.log('\nlineup assignment');
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
const lu = api.lineup(ids(['Jahmyr Gibbs', 'Bijan Robinson', "Ja'Marr Chase", 'Puka Nacua']));
check('two RB two WR fill RB/RB/WR/WR before flex',
  lu.slots.filter(s => s.p).map(s => s.lb).join(' ') === 'RB RB WR WR');

// Walker is ranked 18th and Brown 19th, so FLEX takes Walker and Brown benches:
// the lineup always promotes the better player, not the earlier pick.
const lu3 = api.lineup(ids(['Jahmyr Gibbs', 'Bijan Robinson', 'Chase Brown', 'Kenneth Walker III']));
const benched = lu3.slots.filter(s => s.k === 'BN' && s.p);
check('a fourth RB benches, and the better one takes FLEX',
  benched.length === 1 && benched[0].p.name === 'Chase Brown' &&
  lu3.slots.find(s => s.k === 'FLEX').p.name === 'Kenneth Walker III');
check('nobody is assigned to two slots at once',
  new Set(lu3.slots.filter(s => s.p).map(s => s.p.id)).size === lu3.slots.filter(s => s.p).length);

const n0 = api.needs([]);
check('an empty roster needs every starting slot', n0.open === 10, 'open=' + n0.open);
check('empty roster counts FLEX toward RB, WR and TE',
  n0.need.RB === 3 && n0.need.WR === 4 && n0.need.TE === 2, JSON.stringify(n0.need));

/* ── Keeper validation ──────────────────────────────────────────────────── */
console.log('\nkeeper rules');
const gib = byName['Jahmyr Gibbs'].id, bij = byName['Bijan Robinson'].id;
check('a clean keeper list passes',
  api.keeperProblems([{ team: 0, id: gib, round: 1 }, { team: 1, id: bij, round: 2 }], 12, 16).length === 0);
check('two keepers on one team in one round is rejected',
  api.keeperProblems([{ team: 0, id: gib, round: 3 }, { team: 0, id: bij, round: 3 }], 12, 16).length === 1);
check('the same player kept by two teams is rejected',
  api.keeperProblems([{ team: 0, id: gib, round: 1 }, { team: 1, id: gib, round: 2 }], 12, 16).length === 1);
check('a keeper past the last round is rejected',
  api.keeperProblems([{ team: 0, id: gib, round: 20 }], 12, 16).length === 1);

/* ── Full drafts ────────────────────────────────────────────────────────── */
console.log('\nfull drafts');
function runDraft(over) {
  api.S = settings(over);
  api.S.rounds = api.rounds();
  api.startDraft();
  let guard = 0;
  while (api.St.onClock < api.totalPicks()) {
    if (++guard > 5000) throw new Error('draft failed to terminate');
    const team = api.teamAt(api.St.onClock);
    const p = api.isBot(team) ? api.botChoice(team, false) : api.autoChoice(team);
    if (!p) throw new Error('no pick available at overall ' + api.St.onClock);
    api.makePick(p.id);
  }
  return api.St;
}

const KEEPERS = [
  { team: 0, id: byName['Jahmyr Gibbs'].id, round: 1 },
  { team: 0, id: byName['Brock Bowers'].id, round: 5 },
  { team: 3, id: byName["Ja'Marr Chase"].id, round: 2 },
  { team: 7, id: byName['Puka Nacua'].id, round: 1 },
];

const configs = [
  { label: '12-team standard snake', s: { seats: seats(12, { 4: 'me' }) } },
  { label: '10-team, 3rd round reversal', s: { teams: 10, type: '3rr', seats: seats(10, { 0: 'me' }) } },
  { label: '14-team linear', s: { teams: 14, type: 'linear', seats: seats(14, { 6: 'me' }) } },
  { label: '12-team superflex', s: { seats: seats(12, { 4: 'me' }), slots: Object.assign({}, api.STANDARD, { SFLEX: 1, BN: 5 }) } },
  { label: '8-team, no K or DEF', s: { teams: 8, seats: seats(8, { 0: 'me' }), slots: Object.assign({}, api.STANDARD, { K: 0, DST: 0, BN: 8 }) } },
  { label: '4-team deep bench', s: { teams: 4, seats: seats(4, { 0: 'me' }), slots: Object.assign({}, api.STANDARD, { BN: 12 }) } },
  { label: '12-team with keepers', s: { seats: seats(12, { 4: 'me' }), keepers: KEEPERS } },
  { label: 'no randomness at all', s: { seats: seats(12, { 4: 'me' }), randomness: 0 } },
  { label: 'maximum randomness + keepers',
    s: { seats: seats(12, { 4: 'me' }), randomness: 2.4, keepers: KEEPERS } },
  { label: 'same screen, four humans + keepers',
    s: { mode: 'hotseat', seats: seats(12, { 0: 'me', 1: 'friend', 2: 'friend', 9: 'friend' }), keepers: KEEPERS } },
];

for (const cfg of configs) {
  console.log('\n  ' + cfg.label);
  let st;
  try { st = runDraft(cfg.s); }
  catch (e) { failures++; console.log('  FAIL  threw: ' + e.message); continue; }

  const S = api.S;
  check('every pick made', st.picks.length === api.totalPicks(), st.picks.length + '/' + api.totalPicks());
  check('no player drafted twice', new Set(st.picks.map(p => p.id)).size === st.picks.length);
  check('every roster is full', st.rosters.every(r => r.length === S.rounds),
    st.rosters.map(r => r.length).join(','));
  check('one pick per board slot',
    new Set(st.picks.map(p => p.overall)).size === api.totalPicks());

  const unfilled = st.rosters.map((r, i) => [i, api.needs(r).open]).filter(x => x[1] > 0);
  check('every team fields a full starting lineup', unfilled.length === 0,
    unfilled.map(x => 'team ' + (x[0] + 1) + ' short ' + x[1]).join('; '));

  const over = [];
  st.rosters.forEach((r, i) => {
    const c = api.counts(r);
    if ((c.QB || 0) > (S.slots.SFLEX > 0 ? 3 : 2)) over.push('team ' + (i + 1) + ' has ' + c.QB + ' QB');
    if ((c.TE || 0) > 2) over.push('team ' + (i + 1) + ' has ' + c.TE + ' TE');
    if ((c.K || 0) > Math.max(1, S.slots.K)) over.push('team ' + (i + 1) + ' has ' + c.K + ' K');
    if ((c.DST || 0) > Math.max(1, S.slots.DST)) over.push('team ' + (i + 1) + ' has ' + c.DST + ' DEF');
  });
  check('no team hoards a scarce position', over.length === 0, over.join('; '));

  const late = st.picks.filter(p => !p.keeper && ['K', 'DST'].includes(api.P[p.id].pos));
  if (late.length) {
    const earliest = Math.min(...late.map(p => Math.floor(p.overall / S.teams) + 1));
    check('K/DEF held until the late rounds', earliest >= S.rounds - 3,
      'earliest was round ' + earliest);
  }

  if (S.keepers.length) {
    const kept = st.picks.filter(p => p.keeper);
    check('every keeper is on the board exactly once', kept.length === S.keepers.length,
      kept.length + '/' + S.keepers.length);
    check('each keeper costs its own team that exact round', S.keepers.every(k => {
      const pk = st.picks.find(p => p.keeper && p.id === k.id);
      return pk && pk.team === k.team && Math.floor(pk.overall / S.teams) + 1 === k.round;
    }));
    check('keepers sit on their rosters', S.keepers.every(k => st.rosters[k.team].includes(k.id)));
    const drafted = st.picks.filter(p => !p.keeper).map(p => p.id);
    check('no keeper was also drafted by someone else',
      S.keepers.every(k => !drafted.includes(k.id)));
  }
}

/* ── Editing the board by hand ──────────────────────────────────────────── */
console.log('\nboard editing');
function freshDraft(over) {
  api.S = settings(Object.assign({ teams: 12, seats: seats(12, { 4: 'me' }) }, over));
  api.S.rounds = api.rounds();
  api.startDraft();
  return api.St;
}
function stepPicks(n) {
  for (let i = 0; i < n && api.St.onClock < api.totalPicks(); i++) {
    const t = api.teamAt(api.St.onClock);
    api.makePick((api.isBot(t) ? api.botChoice(t, false) : api.autoChoice(t)).id);
  }
}

// Filling the live square is just making the pick.
freshDraft();
const liveAt = api.St.onClock;
api.assignPick(liveAt, gib0);
check('filling the square on the clock drafts that player',
  api.pickAt(liveAt) && api.pickAt(liveAt).id === gib0 && api.St.onClock === liveAt + 1);
check('the drafted player leaves the board', api.St.taken[gib0] === true);

// Overwriting a past pick swaps the players and frees the old one.
freshDraft();
stepPicks(6);
const past = api.pickAt(2), oldId = past.id, oldTeam = past.team;
const spare = api.available()[0].id;
check('a past square can be overwritten', api.assignPick(2, spare) === true);
check('the replaced player goes back on the board', !api.St.taken[oldId]);
check('the replaced player leaves that roster', !api.St.rosters[oldTeam].includes(oldId));
check('the new player joins that roster', api.St.rosters[oldTeam].includes(spare));
check('overwriting does not change how many picks exist', api.pickAt(2).id === spare &&
  api.St.picks.filter(p => p.overall === 2).length === 1);
check('overwriting does not move the clock', api.St.onClock === 6);

// A player already on a roster cannot be put on a second one.
const takenElsewhere = api.pickAt(0).id;
check('a player already drafted cannot be assigned elsewhere',
  api.assignPick(4, takenElsewhere) === false);
check('the refused assignment changed nothing', api.pickAt(4).id !== takenElsewhere);

// Filling a future square reserves it, and the draft skips it when it arrives.
const st = freshDraft();
const future = api.St.onClock + 25;
const futureTeam = api.teamAt(future);
api.assignPick(future, gib0);
check('a future square can be reserved', api.pickAt(future) && api.pickAt(future).id === gib0);
check('a reserved player is off the board immediately', api.St.taken[gib0] === true);
check('a reserved player is on that roster immediately', st.rosters[futureTeam].includes(gib0));
check('reserving does not move the clock', api.St.onClock < future);
while (api.St.onClock < api.totalPicks()) {
  const t = api.teamAt(api.St.onClock);
  api.makePick((api.isBot(t) ? api.botChoice(t, false) : api.autoChoice(t)).id);
}
check('the reserved square is not drafted over',
  api.St.picks.filter(p => p.overall === future).length === 1 &&
  api.pickAt(future).id === gib0);
check('a draft with a reserved square still fills every square',
  new Set(api.St.picks.map(p => p.overall)).size === api.totalPicks());
check('a draft with a reserved square still fills every roster',
  api.St.rosters.every(r => r.length === api.S.rounds));
check('nobody ends up drafted twice',
  new Set(api.St.picks.map(p => p.id)).size === api.St.picks.length);

// Clearing a reserved square hands the player back.
freshDraft();
const spot = api.St.onClock + 10;
api.assignPick(spot, gib0);
api.assignPick(spot, null);
check('a reserved square can be cleared', !api.pickAt(spot));
check('clearing puts the player back on the board', !api.St.taken[gib0]);
check('clearing frees the roster spot',
  !api.St.rosters[api.teamAt(spot)].includes(gib0));

// Shared rooms only let you set your own live pick.
api.S = settings({ mode: 'online', teams: 8, clock: 0, seats: seats(8, { 2: 'me' }) });
api.S.rounds = api.rounds();
api.normalizeSeats();
api.St = api.newState();
api.St.onClock = api.overallFor(2, 1);
check('online: your own live pick is editable', api.boardEditable(api.St.onClock));
check("online: someone else’s pick is not", !api.boardEditable(api.overallFor(5, 1)));
check('online: a future square of your own is not', !api.boardEditable(api.overallFor(2, 3)));
api.S.mode = 'solo';
check('offline: any square is editable', api.boardEditable(api.overallFor(5, 1)));

/* ── The personal board ─────────────────────────────────────────────────────
   NicksRankings.csv is matched onto the ESPN pool by name at build time. It
   never touches what the bots do; it only drives the strip along the bottom. */
console.log('\npersonal board');
check('the personal board came through the build', api.HAS_MINE);
const ranked = api.P.filter(p => p.mine > 0);
check('every skill player carries a personal rank',
  api.P.filter(p => !p.mine && !['K', 'DST'].includes(p.pos)).length === 0,
  api.P.filter(p => !p.mine && !['K', 'DST'].includes(p.pos)).map(p => p.name).join(', '));
check('kickers and defenses carry none, as intended',
  api.P.filter(p => p.mine && ['K', 'DST'].includes(p.pos)).length === 0);
check('personal ranks are unique', new Set(ranked.map(p => p.mine)).size === ranked.length);
check('the personal board actually disagrees with ESPN',
  ranked.some(p => Math.abs(p.rank - p.mine) >= 10),
  'largest gap ' + Math.max(...ranked.map(p => Math.abs(p.rank - p.mine))));

api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
const top = api.myBoardRows(10);
check('the list is sorted by your rank, not ESPN',
  top.every((p, i) => i === 0 || p.mine > top[i - 1].mine));
check('the list is capped at what you asked for', api.myBoardRows(5).length === 5);

// It has to drop players the moment they are drafted -- that is the whole
// "updates as the draft goes on" part.
const first = api.myBoardRows(1)[0];
api.makePick(first.id);
check('a drafted player leaves the list immediately',
  !api.myBoardRows(30).some(p => p.id === first.id));
check('the next man up moves to the front', api.myBoardRows(1)[0].mine > first.mine);

check('the list can be filtered by position',
  api.myBoardRows(20, 'RB').every(p => p.pos === 'RB'));
check('FLEX means RB, WR and TE',
  api.myBoardRows(20, 'FLEX').every(p => ['RB', 'WR', 'TE'].includes(p.pos)));
check('a filter with nobody on your board comes back empty',
  api.myBoardRows(20, 'K').length === 0);
check('no filter is the whole board', api.myBoardRows(500).length > 200);

/* ── The number beside each player ─────────────────────────────────────────
   It is how many still-available players the ESPN sheet ranks ahead of him --
   roughly how many picks until the room gets to him, since the bots draft off
   that sheet. It has to fall as the board empties. */
console.log('\nespn-ahead count');
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();

let ah = api.espnAhead();
const espn1 = api.P.find(p => p.rank === 1);
const espn5 = api.P.find(p => p.rank === 5);
check('the top player on the sheet has nobody ahead of him', ah[espn1.id] === 0);
check('the fifth has four ahead of him', ah[espn5.id] === 4, ah[espn5.id]);
check('every available player gets a count',
  Object.keys(ah).length === api.P.length);

api.makePick(espn1.id);
ah = api.espnAhead();
check('a drafted player drops out of the counts', ah[espn1.id] === undefined);
check('everyone behind him moves up one', ah[espn5.id] === 3, ah[espn5.id]);
check('the count is the position in what is left',
  api.P.filter(p => !api.St.taken[p.id])
       .every((p, i) => api.espnAhead()[p.id] === i));

/* ── Picks until your next turn ─────────────────────────────────────────────
   Colours the count above: more of the board ahead of him than picks to wait
   means he should survive to your next turn. */
console.log('\npicks until your turn');
api.S = settings({ teams: 12, type: 'snake', seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
// Seat 1 of a 12-team snake picks 1st and 24th, so 22 picks pass in between.
check('the wheel waits the full turn', api.picksUntilMyTurn() === 22,
  api.picksUntilMyTurn());

// Before your first turn it counts the picks in front of you: seat 6 is sixth
// off the board, so five picks happen first.
api.S = settings({ teams: 12, type: 'snake', seats: seats(12, { 5: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
check('a middle seat waits for the picks in front of it',
  api.picksUntilMyTurn() === 5, api.picksUntilMyTurn());

// Once it IS your turn it looks past the pick in hand to the next one, which is
// the question the colouring actually asks: will he still be there next time?
while (api.teamAt(api.St.onClock) !== 5) {
  const t = api.teamAt(api.St.onClock);
  api.makePick(api.botChoice(t, false).id);
}
// Seat 6 picks 6th and 19th in a 12-team snake, so 12 picks pass in between.
check('on your own clock it measures the gap to your next pick',
  api.picksUntilMyTurn() === 12, api.picksUntilMyTurn());

api.S = settings({ teams: 12, type: 'linear', seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
check('a linear draft always waits a full round', api.picksUntilMyTurn() === 11,
  api.picksUntilMyTurn());

api.S = settings({ teams: 12, seats: seats(12, { 0: 'bot' }) });
api.S.rounds = api.rounds();
api.normalizeSeats();
api.startDraft();
api.MySeat = -1;
check('a watcher waits forever', api.picksUntilMyTurn() === Infinity);

/* ── The lists behind the blend ─────────────────────────────────────────── */
console.log('\nsource rankings');
check('the three sources are named as asked',
  api.SOURCES.map(x => x.label).join(', ') ===
  'My blend, DraftKings, Yapper, Jacob Gibbs, ESPN',
  api.SOURCES.map(x => x.label).join(', '));
const onBoard = api.P.filter(p => p.mine > 0);
check('every player on your board has a DraftKings rank',
  onBoard.every(p => p.dk > 0));
check('Yapper covers its top 150 and no more',
  api.P.filter(p => p.yap > 0).length === 150 &&
  api.P.every(p => p.yap === 0 || p.yap <= 150));
check('Jacob Gibbs stops inside his top 199',
  api.P.every(p => p.jg === 0 || p.jg <= 199));
check('a player off a short list reads as zero, not missing',
  api.P.every(p => typeof p.yap === 'number' && typeof p.jg === 'number'));
check('the sources actually disagree with each other',
  onBoard.some(p => p.yap && p.jg && Math.abs(p.yap - p.jg) >= 20));

/* ── Reading a live room ────────────────────────────────────────────────────
   Every client rebuilds the whole draft from the room snapshot, so this is the
   piece that has to be exactly right: picks arrive as a sparse map keyed by
   pick number, and keepers leave gaps in it. */
console.log('\nlive room snapshot');
const STD = Object.assign({}, api.STANDARD);
function snapshot(over) {
  return Object.assign({
    meta: { teams: 8, type: 'snake', rounds: 16, clock: 30, botDelay: 0.6,
            randomness: 1, slots: STD, keepers: [], createdAt: 1 },
    seats: {}, picks: {},
  }, over);
}
const cid = api.clientId();

api.onRoomData(snapshot({
  seats: { 2: { name: 'Nick', cid: cid }, 5: { name: 'Sam', cid: 'someone-else' } },
  picks: { 0: { t: 0, p: 10, k: 0 }, 1: { t: 1, p: 11, k: 0 } },
}));
check('your own claimed seat comes back as yours', api.MySeat === 2, 'seat ' + api.MySeat);
check('another person reads as a friend, not a bot', api.S.seats[5] === 'friend');
check('an unclaimed seat drafts as a bot', api.S.seats[7] === 'bot');
check('the clock lands on the first pick nobody has made', api.St.onClock === 2, api.St.onClock);
check('rosters rebuild from the snapshot',
  api.St.rosters[0].join() === '10' && api.St.rosters[1].join() === '11');
check('drafted players are off the board', api.St.taken[10] && api.St.taken[11]);

// A keeper sits at a pick number ahead of the live one, leaving a hole in the
// map. The clock must stop at the hole, not run past it to the keeper.
api.onRoomData(snapshot({
  meta: { teams: 8, type: 'snake', rounds: 16, clock: 30, botDelay: 0.6, randomness: 1,
          slots: STD, keepers: [[3, gib0, 1]], createdAt: 1 },
  picks: { 0: { t: 0, p: 10, k: 0 }, 3: { t: 3, p: gib0, k: 1 } },
}));
check('a gap left by a keeper stops the clock at the gap', api.St.onClock === 1, api.St.onClock);
check('the keeper is flagged as kept',
  api.St.picks.filter(p => p.keeper).length === 1 && api.pickAt(3).id === gib0);
check('the keeper is already on its roster', api.St.rosters[3].includes(gib0));
check('the keeper is off the board', api.St.taken[gib0] === true);
check('the keeper square is marked spent', api.St.filled[3] === gib0);

// A finished room.
const full = {};
for (let i = 0; i < 8 * 16; i++) full[i] = { t: api.teamAt(i), p: i, k: 0 };
api.onRoomData(snapshot({ picks: full }));
check('a finished room reports the draft complete', api.St.onClock === 8 * 16);

// Nobody has claimed anything yet.
api.onRoomData(snapshot({}));
check('an unclaimed room leaves you watching', api.MySeat === -1);
check('an empty room starts at pick one', api.St.onClock === 0);

/* Room codes avoid the characters people misread aloud. */
let codes = '';
for (let i = 0; i < 200; i++) codes += api.makeCode();
check('room codes are five characters', api.makeCode().length === 5);
check('room codes skip O, 0, I and 1', !/[O0I1]/.test(codes));

/* ── Online room round-trip ─────────────────────────────────────────────── */
console.log('\nonline room');
api.S = settings({ mode: 'online', teams: 8, clock: 0,
                   seats: seats(8, { 0: 'me', 3: 'friend' }), keepers: [KEEPERS[0]] });
api.S.rounds = api.rounds();
api.normalizeSeats();
api.St = api.newState();
api.placeKeepers();
api.runBotsUntilHuman();
const beforeClock = api.St.onClock;
const beforePicks = api.St.picks.length;
check('bots resolve up to the first person',
  api.S.seats[api.teamAt(beforeClock)] !== 'bot', 'stopped on seat ' + api.teamAt(beforeClock));

const wire = JSON.parse(JSON.stringify(api.roomState()));
check('room state serialises to JSON', !!wire && wire.picks.length === beforePicks);

api.restoreRoom(wire);
check('restored room resumes at the same pick', api.St.onClock === beforeClock,
  api.St.onClock + ' vs ' + beforeClock);
check('restored room keeps every pick', api.St.picks.length === beforePicks);
check('restored room keeps the keeper flagged', api.St.picks.filter(p => p.keeper).length === 1);
check('restored rosters match',
  api.St.rosters.reduce((n, r) => n + r.length, 0) === beforePicks);
check('a device with no saved seat watches rather than drafts', api.MySeat === -1);

// A device that claimed a seat gets it back when the room reloads.
api.__claimSeat(3);
api.restoreRoom(JSON.parse(JSON.stringify(wire)));
check('a claimed seat is picked up again on reload', api.MySeat === 3, 'seat ' + api.MySeat);
check('the claimed seat is the only "me" seat',
  api.S.seats.filter(x => x === 'me').length === 1);
check('other human seats stay friends', api.S.seats[0] === 'friend', api.S.seats.join(','));

/* ── Autopick off your own board ────────────────────────────────────────────
   Your rankings replaced the hand-built queue, so a clock running out should
   walk down your board rather than fall straight through to the bots -- but
   only for your own seat, and only where the roster can still use the player. */
console.log('\nautopick');
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();

check('autopick takes the best player on your board',
  api.autoChoice(0).id === api.myBoardRows(1)[0].id);

const wanted = api.myBoardRows(1)[0];
api.St.taken[wanted.id] = true;
check('autopick skips someone already gone', api.autoChoice(0).id !== wanted.id);
check('autopick moves to the next man on your board',
  api.autoChoice(0).id === api.myBoardRows(1)[0].id);

// Another seat has no personal board, so it uses the room's judgement.
const mineNext = api.myBoardRows(1)[0].id;
const theirs = api.autoChoice(3);
check('another seat does not draft off your rankings',
  theirs && theirs.id !== undefined);

// Roster caps bind your autopick exactly as they bind the bots.
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
const caps = api.rosterCaps();
const qbs = api.P.filter(p => p.pos === 'QB').sort((a, b) => a.mine - b.mine);
for (let i = 0; i < caps.QB; i++) api.St.rosters[0].push(qbs[i].id);
qbs.slice(0, caps.QB).forEach(p => { api.St.taken[p.id] = true; });
check('autopick will not exceed the quarterback cap',
  api.autoChoice(0).pos !== 'QB', api.autoChoice(0).pos);

// At the very end your board cannot help -- it holds no kickers or defenses --
// so it has to fall through rather than leaving the slot empty.
api.S = settings({ teams: 2, seats: seats(2, { 0: 'me' }),
                   slots: { QB: 0, RB: 0, WR: 0, TE: 0, FLEX: 0, SFLEX: 0, K: 1, DST: 1, BN: 0 } });
api.S.rounds = api.rounds();
api.startDraft();
const endPick = api.autoChoice(0);
check('autopick falls through to the room when only K and DEF are left to fill',
  endPick && ['K', 'DST'].includes(endPick.pos), endPick ? endPick.pos : 'nothing');

// A build with no personal rankings must still autopick.
const savedMine = api.P.map(p => p.mine);
api.P.forEach(p => { p.mine = 0; });
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
check('autopick still works with no personal board at all', !!api.autoChoice(0));
api.P.forEach((p, i) => { p.mine = savedMine[i]; });

console.log(failures ? '\n' + failures + ' FAILING\n' : '\nall checks passed\n');
process.exit(failures ? 1 : 0);
