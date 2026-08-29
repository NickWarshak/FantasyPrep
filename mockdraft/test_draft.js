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
// Blocks 3 and 4 are rendering and DOM event wiring; 1 and 2 are the engine.
const engine = scripts.slice(0, 2).join('\n');

const stub = `
  function renderAll(){} function renderClockbar(){} function renderClock(){}
  function renderPool(){} function renderRail(){} function renderRecap(){}
  function renderRoomBar(){} function buildFilters(){}
  const __els = {};
  const document = {
    getElementById: (id) => (__els[id] = __els[id] || { hidden: false, textContent: '', value: '',
      classList: { remove(){}, add(){}, toggle(){} } }),
    querySelector: () => null, querySelectorAll: () => [],
    documentElement: { outerHTML: '<html><body><script id="room-state" type="application/json">null<\/script></body></html>' },
  };
  const __store = {};
  const localStorage = { getItem: k => (k in __store ? __store[k] : null),
                         setItem: (k, v) => { __store[k] = String(v); } };
  function setInterval(){ return 1; } function clearInterval(){}
`;

const api = new Function(stub + engine + `
  return { get S(){return S}, set S(v){S=v}, get St(){return St}, set St(v){St=v},
           get MySeat(){return MySeat}, set MySeat(v){MySeat=v},
           teamAt, totalPicks, overallFor, lineup, needs, counts, available,
           botChoice, autoChoice, startDraft, makePick, applyPick, rounds,
           normalizeSeats, isBot, isLocal, teamName, keeperProblems, placeKeepers,
           newState, roomState, restoreRoom, runBotsUntilHuman, freshSettings,
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

/* ── Queue behaviour ────────────────────────────────────────────────────── */
console.log('\nqueue');
api.S = settings({ teams: 12, seats: seats(12, { 0: 'me' }) });
api.S.rounds = api.rounds();
api.startDraft();
api.St.queues[0] = [byName['Brock Bowers'].id, byName['Trey McBride'].id];
check("autopick takes the top of that seat's queue", api.autoChoice(0).name === 'Brock Bowers');
api.St.taken[byName['Brock Bowers'].id] = true;
check('autopick skips a queued player already gone', api.autoChoice(0).name === 'Trey McBride');
api.St.taken[byName['Trey McBride'].id] = true;
check('autopick falls back to the board with an exhausted queue', !!api.autoChoice(0));
check("one seat's queue does not leak into another",
  !api.St.queues[1] || !api.St.queues[1].length);

console.log(failures ? '\n' + failures + ' FAILING\n' : '\nall checks passed\n');
process.exit(failures ? 1 : 0);
