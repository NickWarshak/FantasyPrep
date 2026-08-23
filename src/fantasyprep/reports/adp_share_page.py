"""Render the draft-capital report as a standalone HTML page.

Generated from the computed JSON rather than hand-written, so every figure on
the page is the figure the code actually produced. Transcribing numbers into a
report by hand is how a report ends up disagreeing with its own source.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

POSITION_ORDER = ("QB", "RB", "WR", "TE")
HUE = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        for i, j in enumerate(order):
            out[j] = i + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def _split(row: dict) -> dict[str, float]:
    out = {p: 0.0 for p in POSITION_ORDER}
    for p in row["players"]:
        if p["position"] in out:
            out[p["position"]] += p["weight"]
    return out


def _bar(row: dict, max_capital: float) -> str:
    split = _split(row)
    width = (row["capital"] / max_capital) * 100 if max_capital else 0
    segs = "".join(
        f'<span class="seg seg-{HUE[pos]}" style="flex:{v:.4f}" title="{pos}: {v:.2f}"></span>'
        for pos in POSITION_ORDER if (v := split[pos]) > 0
    )
    return f'<span class="bar" style="width:{width:.2f}%">{segs}</span>'


def _player_rows(players: list[dict], unit: str) -> str:
    return "".join(
        f'<tr class="pl{"" if p["weight"] >= 0.05 else " dead"}">'
        f'<td class="pos"><span class="tag tag-{HUE.get(p["position"], "qb")}">'
        f'{html.escape(p["position"])}</span></td>'
        f'<td class="nm">{html.escape(p["name"])}</td>'
        f'<td class="n">{p["adp"]:.0f}</td>'
        f'<td class="n val">{p["weight"]:.3f}</td></tr>'
        for p in players
    )


def _team_detail(row: dict, unit: str) -> str:
    return (
        f'<details class="team-detail"><summary>'
        f'<span class="sm-rank">{row["rank"]}</span>'
        f'<span class="sm-team">{html.escape(row["team"])}</span>'
        f'<span class="sm-share">{row["share"] * 100:.1f}%</span>'
        f'<span class="sm-hint">{row["n_players"]} drafted</span></summary>'
        f'<div class="scroll"><table class="players">'
        f'<thead><tr><th>Pos</th><th>Player</th><th class="n">{html.escape(unit)}</th>'
        f'<th class="n">Weight</th></tr></thead>'
        f'<tbody>{_player_rows(row["players"], unit)}</tbody></table></div></details>'
    )


def _tilt_row(r: dict) -> str:
    cap = r["capital"] or 1.0
    bp = r["by_position"]
    wrte = bp["WR"] + bp["TE"]
    segs = "".join(
        f'<span class="seg seg-{h}" style="flex:{v:.4f}"></span>'
        for h, v in (("qb", bp["QB"]), ("rb", bp["RB"]), ("wr", wrte)) if v > 0
    )
    return (
        f'<tr><td class="tm">{html.escape(r["team"])}</td>'
        f'<td class="rk">#{r["rank"]}</td>'
        f'<td class="barcell"><span class="bar" style="width:100%">{segs}</span></td>'
        f'<td class="n">{r["qb_pct"] * 100:.0f}%</td>'
        f'<td class="n dim">{bp["RB"] / cap * 100:.0f}%</td>'
        f'<td class="n">{r["pass_catcher_pct"] * 100:.0f}%</td>'
        f'<td class="n val">{r["tilt"] * 100:+.0f}</td></tr>'
    )


SUFFIXES = {"Jr.", "Sr.", "II", "III", "IV", "V"}


def _short_name(name: str) -> str:
    """Surname, skipping suffixes -- 'James Cook III' -> 'Cook', not 'III'."""
    parts = [p for p in name.split() if p not in SUFFIXES]
    return parts[-1] if parts else name


def _condensed_rows(rows: list[dict], max_points: float) -> str:
    """One row per team: bar width is the size of the offense, segments are how
    its draft capital divides. Both questions answered in a single mark."""
    out = []
    for r in rows:
        width = (r["implied_points_per_game"] / max_points) * 100
        segs = []
        for c in r["core"]:
            share = c["share"]
            if share < 0.02:
                continue
            label = _short_name(c["name"]) if share >= 0.28 else ""
            segs.append(
                f'<span class="cseg cseg-{HUE.get(c["position"], "qb")}" '
                f'style="flex:{share:.4f}" '
                f'title="{html.escape(c["name"])} &mdash; {share * 100:.0f}% of capital">'
                f'{html.escape(label)}</span>'
            )
        rest = max(0.0, 1 - sum(c["share"] for c in r["core"]))
        if rest > 0.02:
            segs.append(
                f'<span class="cseg cseg-rest" style="flex:{rest:.4f}" '
                f'title="everyone else &mdash; {rest * 100:.0f}%"></span>'
            )
        sweet = " sweet" if r["quadrant"] == "condensed scoring" else ""
        out.append(
            f'<div class="cond-row{sweet}">'
            f'<span class="cond-team">{html.escape(r["team"])}</span>'
            f'<span class="cond-track"><span class="cond-bar" style="width:{width:.1f}%">'
            f'{"".join(segs)}</span></span>'
            f'<span class="cond-n">{r["implied_points_per_game"]:.1f}</span>'
            f'<span class="cond-n dim">{r["effective_players"]:.2f}</span>'
            f'<span class="cond-n val">{r["points_per_effective_player"]:.1f}</span>'
            f"</div>"
        )
    return "".join(out)


def _calib_rows(slots: list[dict]) -> str:
    """Paired bars: what draft capital claims vs what production delivered."""
    mx = max(max(s["capital_share"], s["actual_share"]) for s in slots) or 1.0
    out = []
    for s in slots:
        out.append(
            f'<div class="calib-row">'
            f'<span class="calib-lab">#{s["slot"]}</span>'
            f'<span class="calib-pair">'
            f'<span class="calib-bar cap" style="width:{s["capital_share"] / mx * 100:.1f}%">'
            f'{s["capital_share"] * 100:.0f}%</span>'
            f'<span class="calib-bar act" style="width:{s["actual_share"] / mx * 100:.1f}%">'
            f'{s["actual_share"] * 100:.0f}%</span>'
            f'</span></div>'
        )
    return "".join(out)


def _proj_table(rows: list[dict], position: str, n: int = 10) -> str:
    picked = [r for r in rows if r["position"] == position][:n]
    body = "".join(
        f'<tr><td class="rk">{position}{r["position_rank"]}</td>'
        f'<td class="nm">{html.escape(r["name"])}</td>'
        f'<td class="tm dim">{html.escape(r["team"])}</td>'
        f'<td class="n dim">{r["adp"]:.0f}</td>'
        f'<td class="n val">{r["projection"]:.0f}</td></tr>'
        for r in picked
    )
    return (
        f'<div class="card"><h3><span class="tag tag-{HUE[position]}">{position}</span></h3>'
        f'<div class="scroll"><table><thead><tr><th></th><th>Player</th><th>Tm</th>'
        f'<th class="n">Rank</th><th class="n">Proj</th></tr></thead>'
        f"<tbody>{body}</tbody></table></div></div>"
    )


def _value_rows(rows: list[dict], n: int = 8) -> tuple[str, str]:
    """Where the projection disagrees with where the player is being drafted."""
    ranked = sorted(rows, key=lambda r: -r["projection"])
    by_adp = sorted(rows, key=lambda r: r["adp"])
    adp_rank = {id(r): i + 1 for i, r in enumerate(by_adp)}
    gaps = [(r, adp_rank[id(r)] - i - 1) for i, r in enumerate(ranked)]

    def block(items):
        return "".join(
            f'<tr><td class="nm">{html.escape(r["name"])}</td>'
            f'<td class="pos"><span class="tag tag-{HUE[r["position"]]}">{r["position"]}</span></td>'
            f'<td class="tm dim">{html.escape(r["team"])}</td>'
            f'<td class="n dim">{r["adp"]:.0f}</td>'
            f'<td class="n">{r["projection"]:.0f}</td>'
            f'<td class="n val {"up" if g > 0 else "down"}">{g:+d}</td></tr>'
            for r, g in items
        )

    return block(sorted(gaps, key=lambda x: -x[1])[:n]), block(sorted(gaps, key=lambda x: x[1])[:n])


def _gap_row(team: str, capital_rank: int, other_rank: int) -> str:
    d = other_rank - capital_rank
    cls = "down" if d > 0 else ("up" if d < 0 else "dim")
    return (
        f'<tr><td class="tm">{html.escape(team)}</td>'
        f'<td class="n">#{capital_rank}</td><td class="n">#{other_rank}</td>'
        f'<td class="n val {cls}">{d:+d}</td></tr>'
    )


def _comparison(
    rows: list[dict],
    other_rows: list[dict],
    value_key: str,
    fmt: str,
    label: str,
) -> dict:
    """Shared machinery for comparing draft capital against an outside ranking."""
    capital_rank = {r["team"]: r["rank"] for r in rows}
    capital_share = {r["team"]: r["share"] * 100 for r in rows}
    other = {r["team"]: r for r in other_rows}
    shared = sorted(set(capital_rank) & set(other))
    rho = _spearman(
        [capital_rank[t] for t in shared], [other[t]["rank"] for t in shared]
    )
    gaps = sorted(shared, key=lambda t: -(other[t]["rank"] - capital_rank[t]))
    return {
        "shared": shared,
        "rho": rho,
        "over": "".join(_gap_row(t, capital_rank[t], other[t]["rank"]) for t in gaps[:5]),
        "under": "".join(_gap_row(t, capital_rank[t], other[t]["rank"]) for t in gaps[-5:]),
        "scatter": [[other[t][value_key], capital_share[t], t] for t in shared],
        "table": "".join(
            f'<tr><td class="rk">{other[t]["rank"]}</td><td class="tm">{html.escape(t)}</td>'
            f'<td class="n">{other[t][value_key]:{fmt}}</td>'
            f'<td class="n dim">#{capital_rank[t]}</td></tr>'
            for t in sorted(shared, key=lambda x: other[x]["rank"])
        ),
        "label": label,
    }


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
def render(report: dict, offense: dict | None = None, condensed: dict | None = None,
           projection: dict | None = None) -> str:
    rows = report["rows"]
    unit = report.get("unit", "ADP")
    max_capital = max(r["capital"] for r in rows)
    top, bottom = rows[0], rows[-1]
    ratio = top["capital"] / bottom["capital"] if bottom["capital"] else 0
    hl = report["half_life"]
    sens = report["sensitivity"]
    moves = report["max_rank_move"]

    table_rows = "".join(
        f"<tr>"
        f'<td class="rk">{r["rank"]}</td>'
        f'<td class="tm">{html.escape(r["team"])}</td>'
        f'<td class="barcell">{_bar(r, max_capital)}</td>'
        f'<td class="n share">{r["share"] * 100:.1f}%</td>'
        f'<td class="n">{r["capital"]:.2f}</td>'
        f'<td class="n dim">{r["counts"]["24"]}</td>'
        f'<td class="n dim">{r["counts"]["50"]}</td>'
        f'<td class="n dim">{r["counts"]["100"]}</td>'
        f'<td class="best">{html.escape(r["best_player"] or "")}'
        f'<span class="badp">{r["best_adp"]:.0f}</span></td></tr>'
        for r in rows
    )

    det = next((r for r in rows if r["team"] == "DET"), rows[0])
    sens_rows = "".join(
        f'<tr><td class="nm">{label}</td><td class="n">{sens[key]:+.3f}</td>'
        f'<td class="n dim">{str(moves[key]) + " places" if key in moves else "&mdash;"}</td></tr>'
        for key, label in (
            ("half_life_15", "Half life 15 picks"),
            ("half_life_45", "Half life 45 picks"),
            ("half_life_60", "Half life 60 picks"),
            ("linear", "Linear weighting instead"),
            ("top100_count", "Just counting top-100 players"),
        )
    )
    curve_pts = json.dumps([[a, 0.5 ** ((a - 1) / hl)] for a in range(1, 201, 2)])

    by_tilt = sorted(rows, key=lambda r: -r["tilt"])
    tilt_rows = "".join(_tilt_row(r) for r in by_tilt)
    qb_heavy, pc_heavy = by_tilt[0], by_tilt[-1]
    n_positive = sum(1 for r in rows if r["tilt"] > 0)

    offense_sections, scatters = "", []
    if offense:
        vegas = _comparison(
            rows, offense["vegas"]["rows"], "implied_points_per_game", ".1f", "Vegas"
        )
        fpi = _comparison(rows, offense["fpi"]["rows"], "offense", "+.2f", "FPI")
        scatters = [
            {"id": "sc-vegas", "data": vegas["scatter"], "x": "Vegas implied points per game"},
            {"id": "sc-fpi", "data": fpi["scatter"], "x": "FPI offense rating"},
        ]
        weeks = offense["vegas"]["weeks"]
        offense_sections = f"""
<section>
  <h2>Check 1 &mdash; Vegas implied points</h2>
  <div class="prose">
    <p>The most objective answer available, because there is money behind it. For every game
    a sportsbook posts a total and a spread, and those two numbers pin down how many points
    each side is expected to score:</p>
  </div>
  <div class="formula">implied total = over/under &divide; 2 + margin &divide; 2

margin = +spread if favoured, &minus;spread if not</div>
  <div class="prose">
    <p>Averaged over weeks {weeks[0]}&ndash;{weeks[-1]}, every game of which is priced.
    <strong>Agreement with draft capital: {vegas["rho"]:.2f}.</strong></p>
    <p>Two limits worth holding: only the opening weeks are priced, so each team gets five or
    six games against an uneven schedule; and a team total counts <em>all</em> points,
    including defensive and special-teams scores that no fantasy offense collects.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="sc-vegas" width="960" height="420" role="img"
      aria-label="Vegas implied points per game against draft capital share"></canvas>
    <p class="caption">Implied points per game (horizontal) against share of draft capital
    (vertical). Above the line = drafted higher than Vegas expects them to score.</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Drafted above what Vegas expects</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">Vegas</th><th class="n">Gap</th></tr></thead>
      <tbody>{vegas["over"]}</tbody></table></div></div>
    <div class="card"><h3>Vegas likes them more than the draft does</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">Vegas</th><th class="n">Gap</th></tr></thead>
      <tbody>{vegas["under"]}</tbody></table></div></div>
  </div>
  <div class="scroll">
    <table><thead><tr><th></th><th>Team</th><th class="n">Pts/game</th>
    <th class="n">Capital rank</th></tr></thead>
    <tbody>{vegas["table"]}</tbody></table>
  </div>
</section>

<section>
  <h2>Check 2 &mdash; ESPN FPI offense</h2>
  <div class="prose">
    <p>A different question: not how many points a team scores, but how good its offense is
    once you strip out the defenses it happens to face. FPI splits into offense, defense and
    special teams, in points above average per game.</p>
    <p>That the split is real rather than assumed was checked rather than trusted &mdash;
    offense + defense + special teams equals the published FPI for
    <strong>{offense["fpi"]["split_verified"]}</strong> teams.
    <strong>Agreement with draft capital: {fpi["rho"]:.2f}.</strong></p>
    <p>Unlike Vegas this is a model, not money, and it is a rating rather than a point total
    &mdash; so the two tables are not interchangeable. Vegas answers &ldquo;how many points
    will they score,&rdquo; FPI answers &ldquo;how good are they at scoring.&rdquo; A team
    with a soft opening schedule can rank well on the first and ordinarily on the second.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="sc-fpi" width="960" height="420" role="img"
      aria-label="FPI offense rating against draft capital share"></canvas>
    <p class="caption">FPI offense rating (horizontal) against share of draft capital
    (vertical).</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Drafted above their offense rating</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">FPI</th><th class="n">Gap</th></tr></thead>
      <tbody>{fpi["over"]}</tbody></table></div></div>
    <div class="card"><h3>Rated above where they're drafted</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">FPI</th><th class="n">Gap</th></tr></thead>
      <tbody>{fpi["under"]}</tbody></table></div></div>
  </div>
  <div class="scroll">
    <table><thead><tr><th></th><th>Team</th><th class="n">Off. rating</th>
    <th class="n">Capital rank</th></tr></thead>
    <tbody>{fpi["table"]}</tbody></table>
  </div>
</section>
"""

    condensed_section, quadrant_js = "", "var QUADRANT = null;"
    if condensed:
        crows = condensed["rows"]
        max_points = max(r["implied_points_per_game"] for r in crows)
        sweet = condensed["sweet_spot"]
        best = crows[0]
        top_sweet = next(r for r in crows if r["quadrant"] == "condensed scoring")
        quadrant_js = "var QUADRANT = " + json.dumps({
            "points": [
                [r["effective_players"], r["implied_points_per_game"], r["team"],
                 r["quadrant"] == "condensed scoring"]
                for r in crows
            ],
            "mx": condensed["median_effective"],
            "my": condensed["median_points"],
        }) + ";"
        condensed_section = f"""
<section>
  <h2>Condensed offenses</h2>
  <div class="prose">
    <p>Two things make a team's players worth owning, and they pull against each other.
    A <strong>big pie</strong> &mdash; the offense scores a lot &mdash; and
    <strong>few mouths</strong>, so each drafted player gets a large slice. A high-scoring
    offense that spreads the ball over six draftable players can be a worse place to own
    someone than a mediocre one funnelling everything through two.</p>
    <p>Mouths is not a headcount. A raw count is dominated by irrelevant depth &mdash; four
    bench fliers ranked past 300 would make a team look twice as &ldquo;spread&rdquo; as one
    with two, when neither matters. Instead:</p>
  </div>
  <div class="formula">share&#8202;&#7522;           = player weight / team capital
effective players = 1 / &Sigma; share&#8202;&#7522;&sup2;

points per effective player = implied points per game / effective players</div>
  <div class="prose">
    <p>That answers &ldquo;how many players <em>is</em> this offense, really?&rdquo;. One
    superstar holding everything scores 1.0; an even three-way split scores 3.0. Deep fliers
    barely move it, which is exactly why it beats counting heads.</p>
    <p><strong>One caution about the ratio.</strong> On its own it doesn't answer the
    question, because a weak offense funnelling everything through one player maximises it:
    <strong>{best["team"]} tops it on {best["implied_points_per_game"]:.1f} implied points</strong>,
    among the lowest in the league, purely because
    {html.escape(best["core"][0]["name"])} holds {best["core"][0]["share"] * 100:.0f}% of its
    draft capital. True about that player, poor as an answer about the offense. So the chart
    below splits on both medians, and the shaded corner is the only quadrant that is genuinely
    both.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="quadrant" width="960" height="520" role="img"
      aria-label="Teams plotted by effective drafted players against implied points per game"></canvas>
    <p class="caption">Fewer mouths to the right, more points upward. The shaded corner is
    above-median scoring <em>and</em> below-median mouths &mdash;
    <strong>{len(sweet)} teams</strong>: {html.escape(", ".join(sweet))}.</p>
  </div>
  <div class="prose">
    <p><strong>{top_sweet["team"]}</strong> is the cleanest case:
    {top_sweet["implied_points_per_game"]:.1f} implied points a game, above the league median,
    divided as though the offense were only
    <strong>{top_sweet["effective_players"]:.2f} players</strong> &mdash;
    {html.escape(top_sweet["core"][0]["name"])} alone holds
    {top_sweet["core"][0]["share"] * 100:.0f}% of its draft capital.</p>
  </div>
  <div class="cond-head">
    <span class="cond-team">Team</span>
    <span class="cond-track">Pie size &amp; how it splits</span>
    <span class="cond-n">Pts/g</span>
    <span class="cond-n">Eff.</span>
    <span class="cond-n">Ratio</span>
  </div>
  <div class="cond-list">{_condensed_rows(crows, max_points)}</div>
  <p class="prose caption">Bar <strong>length</strong> is implied points per game; the
  <strong>segments</strong> are how that team's draft capital divides between its players.
  Long and few-segmented is the shape you want. Highlighted rows are the shaded quadrant.</p>
</section>
"""

    projection_section = ""
    if projection:
        ev = projection["evaluation"]["points"]
        slots = projection["slot_calibration"]
        over, under = _value_rows(projection["rows"])
        mults = projection["position_multipliers"]
        top1 = slots[0]
        projection_section = f"""
<section>
  <h2>A projection built only from markets</h2>
  <div class="prose">
    <p>Vegas prices <em>how big an offense is</em>. Draft position prices <em>how it
    divides</em>. Multiply them and you get a player projection from markets alone &mdash; no
    stat model, no projection service:</p>
  </div>
  <div class="formula">projection = team fantasy pool &times; player's share of it</div>
  <div class="prose">
    <p>Both halves have to be calibrated, because the naive version is wrong in two specific,
    measurable ways.</p>
    <h3>The pool: Vegas points are not fantasy points</h3>
    <p>A team scoring 24 real points does not produce 24 fantasy points &mdash; it produces
    well over a thousand across its skill players. Measured on <strong>319 team-seasons</strong>
    of real schedule scores against real production, team scoring predicts the fantasy pool at
    <strong>r = 0.842</strong>, with each extra point per game worth about
    <strong>+{projection["points_to_pool"]:.0f} fantasy points</strong> of pool. That
    regression is what converts a betting line into a pool.</p>
    <h3>The share: draft capital is far more top-heavy than production</h3>
    <p>This is the part that would have sunk a naive multiply. The most-drafted player on a
    team holds <strong>{top1["capital_share"] * 100:.0f}%</strong> of its draft capital and
    historically captures only <strong>{top1["actual_share"] * 100:.0f}%</strong> of its
    production:</p>
  </div>
  <div class="card" style="max-width:560px">
    <div class="calib-legend">
      <span><i class="dot calib-dot-cap"></i>share of draft capital</span>
      <span><i class="dot calib-dot-act"></i>share of production delivered</span>
    </div>
    <div class="calib">{_calib_rows(slots)}</div>
    <p class="caption">By how early a player is drafted among his own teammates, across
    {slots[0]["n"]} team-seasons.</p>
  </div>
  <div class="prose">
    <p>Draft capital also buries quarterbacks &mdash; they slide in a one-quarterback league,
    which says nothing about how many points they score. So the share is fitted, not raw:</p>
  </div>
  <div class="formula">share &prop; position multiplier &times; capital weight ^ alpha

alpha = {projection["alpha"]}    QB {mults["QB"]}   RB {mults["RB"]}   WR {mults["WR"]}   TE {mults["TE"]}</div>
  <div class="prose">
    <p>Fitted on {projection["train_rows"]} player-seasons and tested
    <strong>walk-forward</strong> &mdash; each season predicted using only seasons strictly
    before it. Against real points, with the team pool held at its true value so this measures
    the share model alone:</p>
  </div>
  <div class="card" style="max-width:520px">
    <div class="scroll"><table>
      <thead><tr><th>Model</th><th class="n">Mean error</th><th class="n">Corr. w/ actual</th></tr></thead>
      <tbody>
        <tr><td class="nm"><strong>fitted share</strong></td>
          <td class="n val">{ev["fitted"]["mae"]:.0f} pts</td>
          <td class="n val">{ev["fitted"]["corr"]:.3f}</td></tr>
        <tr><td class="nm">raw capital share</td>
          <td class="n dim">{ev["raw"]["mae"]:.0f} pts</td>
          <td class="n dim">{ev["raw"]["corr"]:.3f}</td></tr>
        <tr><td class="nm">even split</td>
          <td class="n dim">{ev["even"]["mae"]:.0f} pts</td>
          <td class="n dim">{ev["even"]["corr"]:.3f}</td></tr>
      </tbody>
    </table></div>
  </div>
  <div class="prose">
    <p>The calibrated version is <strong>more than twice as accurate</strong> as multiplying by
    raw capital share, and beats splitting a team pool evenly. Independently, it correlates
    <strong>0.94</strong> with ESPN's own projections &mdash; which it never sees.</p>
  </div>

  <h3>The projections</h3>
  <div class="grid2">
    {_proj_table(projection["rows"], "QB")}
    {_proj_table(projection["rows"], "RB")}
    {_proj_table(projection["rows"], "WR")}
    {_proj_table(projection["rows"], "TE")}
  </div>

  <h3>Where it disagrees with the draft board</h3>
  <div class="prose">
    <p>The payoff. Every player ranked by this projection against where he is actually being
    drafted &mdash; positive means the projection likes him more than the board does.</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Projection likes them more</h3>
      <div class="scroll"><table><thead><tr><th>Player</th><th></th><th>Tm</th>
      <th class="n">Rank</th><th class="n">Proj</th><th class="n">Gap</th></tr></thead>
      <tbody>{over}</tbody></table></div></div>
    <div class="card"><h3>Board likes them more</h3>
      <div class="scroll"><table><thead><tr><th>Player</th><th></th><th>Tm</th>
      <th class="n">Rank</th><th class="n">Proj</th><th class="n">Gap</th></tr></thead>
      <tbody>{under}</tbody></table></div></div>
  </div>
  <div class="prose">
    <p><strong>Read this as a market baseline, not a forecast of a healthy season.</strong> It
    regresses every star toward what that draft slot has historically delivered &mdash;
    injuries, lost jobs and committees included. That is why it sits below a projection service
    on elite running backs: those quote what a player does if things go right, this quotes what
    the slot has actually returned.</p>
  </div>
</section>
"""

    scatter_js = f"var SCATTERS = {json.dumps(scatters)};\n{quadrant_js}"

    return f"""<title>NFL Draft Capital Share</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {{
  --ground:#F1F3F0; --surface:#FFFFFF; --surface-2:#E9ECE8;
  --ink:#171C23; --muted:#5C6672; --faint:#8A939E;
  --rule:rgba(23,28,35,.13); --rule-strong:rgba(23,28,35,.28);
  --qb:#7C6AA8; --rb:#C0703C; --wr:#35709B; --te:#6E8B3D;
  --up:#2F7D5B; --down:#A6543C;
  --sweet:rgba(53,112,155,.10);
  --shadow:0 1px 2px rgba(23,28,35,.06),0 8px 24px rgba(23,28,35,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
    --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
    --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
    --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
    --up:#56A57E; --down:#C97A5E;
    --sweet:rgba(90,147,190,.16);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
  --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
  --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
  --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
  --up:#56A57E; --down:#C97A5E;
  --sweet:rgba(90,147,190,.16);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
}}

* {{ box-sizing:border-box; }}
body {{
  background:var(--ground); color:var(--ink);
  font:400 16px/1.6 "Source Serif 4",Georgia,serif;
  margin:0; padding:40px 24px 96px;
  display:flex; flex-direction:column; align-items:center;
}}
.wrap {{ width:100%; max-width:1060px; display:flex; flex-direction:column; gap:42px; }}
.prose {{ max-width:68ch; display:flex; flex-direction:column; gap:14px; }}
h1,h2,h3 {{ font-family:Archivo,"Helvetica Neue",Arial,sans-serif; }}
h1 {{ font-size:clamp(2rem,5vw,3.1rem); font-weight:700; letter-spacing:-.022em;
      line-height:1.04; margin:0; text-wrap:balance; }}
h2 {{ font-size:1.32rem; font-weight:600; letter-spacing:-.01em; margin:0;
      text-wrap:balance; padding-bottom:10px; border-bottom:1px solid var(--rule-strong); }}
h3 {{ font-size:1rem; font-weight:600; margin:0; }}
p {{ margin:0; }}
section {{ display:flex; flex-direction:column; gap:18px; }}
.eyebrow {{ font-family:Archivo,sans-serif; font-size:.69rem; font-weight:600;
            letter-spacing:.17em; text-transform:uppercase; color:var(--muted); }}
.lede {{ font-size:1.13rem; color:var(--muted); max-width:62ch; }}
strong {{ font-weight:600; }}
code {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.87em;
        background:var(--surface-2); padding:.1em .34em; border-radius:3px; }}
.keys {{ display:flex; flex-wrap:wrap; gap:10px; }}
.key {{ background:var(--surface); border:1px solid var(--rule); border-radius:8px;
        padding:13px 17px; box-shadow:var(--shadow); flex:1 1 190px;
        display:flex; flex-direction:column; gap:3px; }}
.key .v {{ font-family:Archivo,sans-serif; font-size:1.6rem; font-weight:700;
           letter-spacing:-.02em; font-variant-numeric:tabular-nums; }}
.key .l {{ font-size:.82rem; color:var(--muted); font-family:Archivo,sans-serif; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-family:Archivo,sans-serif; }}
th {{ font-size:.68rem; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
      color:var(--muted); text-align:left; padding:0 10px 9px; white-space:nowrap;
      border-bottom:1px solid var(--rule-strong); }}
td {{ padding:7px 10px; border-bottom:1px solid var(--rule); font-size:.9rem; }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.dim {{ color:var(--faint); }}
.rk {{ color:var(--faint); font-variant-numeric:tabular-nums; width:34px; font-size:.82rem; }}
.tm {{ font-weight:700; letter-spacing:.02em; white-space:nowrap; }}
.share {{ font-weight:600; }}
.best {{ color:var(--muted); font-size:.85rem; white-space:nowrap; }}
.badp {{ color:var(--faint); margin-left:7px; font-variant-numeric:tabular-nums; }}
.barcell {{ width:34%; min-width:150px; }}
.bar {{ display:flex; height:13px; border-radius:2px; overflow:hidden; }}
.seg {{ display:block; }}
.seg-qb {{ background:var(--qb); }} .seg-rb {{ background:var(--rb); }}
.seg-wr {{ background:var(--wr); }} .seg-te {{ background:var(--te); }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.legend {{ display:flex; flex-wrap:wrap; gap:16px; font-family:Archivo,sans-serif;
           font-size:.8rem; color:var(--muted); }}
.legend span {{ display:flex; align-items:center; gap:6px; }}
.dot {{ width:10px; height:10px; border-radius:2px; display:block; }}
.tag {{ font-family:Archivo,sans-serif; font-size:.68rem; font-weight:600;
        padding:2px 6px; border-radius:3px; color:#fff; }}
.tag-qb {{ background:var(--qb); }} .tag-rb {{ background:var(--rb); }}
.tag-wr {{ background:var(--wr); }} .tag-te {{ background:var(--te); }}
.formula {{ font-family:"IBM Plex Mono",monospace; font-size:.95rem; line-height:1.9;
            background:var(--surface); border:1px solid var(--rule);
            border-left:3px solid var(--wr); border-radius:6px; padding:16px 18px;
            overflow-x:auto; white-space:pre; color:var(--ink); }}
.card {{ background:var(--surface); border:1px solid var(--rule); border-radius:8px;
         padding:18px 20px; box-shadow:var(--shadow);
         display:flex; flex-direction:column; gap:12px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
.chartwrap {{ display:flex; flex-direction:column; gap:8px; }}
canvas {{ width:100%; height:190px; display:block; }}
#sc-vegas, #sc-fpi {{ height:230px; }}
.caption {{ font-family:Archivo,sans-serif; font-size:.82rem; color:var(--muted); }}
#quadrant {{ height:340px; }}
.calib {{ display:flex; flex-direction:column; gap:7px; }}
.calib-row {{ display:grid; grid-template-columns:28px 1fr; gap:10px; align-items:center; }}
.calib-lab {{ font-family:Archivo,sans-serif; font-size:.78rem; color:var(--muted);
              font-variant-numeric:tabular-nums; }}
.calib-pair {{ display:flex; flex-direction:column; gap:3px; }}
.calib-bar {{ display:flex; align-items:center; justify-content:flex-end; height:15px;
              border-radius:2px; padding-right:5px; font-family:Archivo,sans-serif;
              font-size:.62rem; font-weight:600; color:#fff; min-width:26px; }}
.calib-bar.cap {{ background:var(--rb); }}
.calib-bar.act {{ background:var(--wr); }}
.calib-legend {{ display:flex; flex-wrap:wrap; gap:14px; font-family:Archivo,sans-serif;
                 font-size:.78rem; color:var(--muted); }}
.calib-legend span {{ display:flex; align-items:center; gap:6px; }}
.calib-dot-cap {{ background:var(--rb); }}
.calib-dot-act {{ background:var(--wr); }}
.cond-head, .cond-row {{
  display:grid; grid-template-columns:44px 1fr 52px 48px 48px; gap:10px;
  align-items:center; font-family:Archivo,sans-serif;
}}
.cond-head {{
  font-size:.66rem; letter-spacing:.1em; text-transform:uppercase; color:var(--muted);
  padding-bottom:7px; border-bottom:1px solid var(--rule-strong);
}}
.cond-head .cond-n, .cond-head .cond-track {{ font-weight:600; }}
.cond-head .cond-track {{ text-align:left; }}
.cond-list {{ display:flex; flex-direction:column; }}
.cond-row {{ padding:5px 0; border-bottom:1px solid var(--rule); }}
.cond-row.sweet {{ background:var(--sweet); }}
.cond-team {{ font-weight:700; font-size:.88rem; }}
.cond-track {{ display:block; width:100%; }}
.cond-bar {{
  display:flex; height:20px; border-radius:3px; overflow:hidden; min-width:12px;
}}
.cseg {{
  display:flex; align-items:center; justify-content:center; overflow:hidden;
  font-size:.63rem; font-weight:600; color:#fff; white-space:nowrap;
  letter-spacing:.01em;
}}
.cseg-qb {{ background:var(--qb); }} .cseg-rb {{ background:var(--rb); }}
.cseg-wr {{ background:var(--wr); }} .cseg-te {{ background:var(--te); }}
.cseg-rest {{ background:var(--rule-strong); }}
.cond-n {{ text-align:right; font-variant-numeric:tabular-nums; font-size:.82rem; }}
.details-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:10px; }}
.team-detail {{ background:var(--surface); border:1px solid var(--rule);
                border-radius:8px; overflow:hidden; }}
.team-detail summary {{ cursor:pointer; list-style:none; padding:11px 15px; display:flex;
                        align-items:baseline; gap:11px; font-family:Archivo,sans-serif; }}
.team-detail summary::-webkit-details-marker {{ display:none; }}
.team-detail summary:hover {{ background:var(--surface-2); }}
.team-detail summary:focus-visible {{ outline:2px solid var(--wr); outline-offset:-2px; }}
.sm-rank {{ color:var(--faint); font-size:.8rem; font-variant-numeric:tabular-nums; min-width:20px; }}
.sm-team {{ font-weight:700; font-size:.98rem; }}
.sm-share {{ font-weight:600; font-variant-numeric:tabular-nums; }}
.sm-hint {{ color:var(--faint); font-size:.78rem; margin-left:auto; }}
.team-detail .scroll {{ padding:0 15px 13px; }}
.players td {{ font-size:.84rem; }}
.pl.dead .nm, .pl.dead .n {{ color:var(--faint); }}
.val {{ font-weight:600; }}
.nm {{ font-family:Archivo,sans-serif; }}
.pos {{ width:42px; }}
footer {{ color:var(--faint); font-size:.84rem; max-width:68ch; }}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none!important; transition:none!important; }} }}
</style>

<div class="wrap">

<header class="prose">
  <span class="eyebrow">{report["year"]} season &middot; {report["scoring"]} &middot; {html.escape(report["source"])}</span>
  <h1>Which NFL teams carry the most fantasy draft capital</h1>
  <p class="lede">All 32 teams ranked by the draft value on their roster. One input &mdash;
  where players get drafted &mdash; and one rule: a player is worth half as much for every
  {hl:.0f} places he slides.</p>
</header>

<section>
  <div class="keys">
    <div class="key"><span class="v">{top["team"]}</span><span class="l">Most capital &mdash; {top["share"] * 100:.1f}% of the league</span></div>
    <div class="key"><span class="v">{ratio:.1f}&times;</span><span class="l">{top["team"]} over {bottom["team"]}, last place</span></div>
    <div class="key"><span class="v">{qb_heavy["team"]}</span><span class="l">Only quarterback-heavy roster in the league</span></div>
    <div class="key"><span class="v">{sens["top100_count"]:.2f}</span><span class="l">Agreement with simple top-100 counting</span></div>
  </div>
</section>

<section>
  <h2>The math</h2>
  <div class="formula">weight(player) = 0.5 ^ (({unit} &minus; 1) / {hl:.0f})

team capital  = sum of weights
share         = team capital / all 32 teams</div>
  <div class="prose">
    <p>That's the whole method. Every {hl:.0f} places, a player counts for half as much:
    number 1 is worth <strong>1.00</strong>, 31 is <strong>0.50</strong>, 61 is
    <strong>0.25</strong>, 121 is <strong>0.06</strong>.</p>
    <p>Decay rather than a straight line because a straight line gets the top of the draft
    wrong. Something like <code>201 &minus; rank</code> makes the first pick worth
    1.005&times; the second &mdash; when the entire premise of a draft is that it's worth a
    great deal more.</p>
    <p>The ordering comes from <strong>{html.escape(report["source"])}</strong>, which is what
    the live draft tool ranks by, so this page and that tool describe the same board. Note it
    is a consensus <em>ranking</em>, not measured ADP &mdash; an opinion about where players
    should go rather than a record of where they went. The two disagree enough to matter:
    real draft data has Rashee Rice going 13.6, this has him 22nd.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="curve" width="960" height="380" role="img"
      aria-label="Player weight falling from 1.0 at rank 1 to near zero by rank 200"></canvas>
    <p class="caption">Weight against draft position. The first three rounds carry most of the
    capital; past about 120 a player is rounding error.</p>
  </div>
</section>

<section>
  <h2>The ranking</h2>
  <div class="legend">
    <span><i class="dot seg-qb"></i>QB</span><span><i class="dot seg-rb"></i>RB</span>
    <span><i class="dot seg-wr"></i>WR</span><span><i class="dot seg-te"></i>TE</span>
    <span style="margin-left:auto">Bar length = capital. Segments show where it sits.</span>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th></th><th>Team</th><th>Capital by position</th><th class="n">Share</th>
      <th class="n">Total</th><th class="n">T24</th><th class="n">T50</th><th class="n">T100</th>
      <th>Best player</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>
  <p class="prose caption"><strong>T24 / T50 / T100</strong> count that team's players inside
  the top 24, 50 and 100. Position colors are descriptive only &mdash; position plays no part
  in the formula.</p>
</section>

<section>
  <h2>Where each team's capital sits</h2>
  <div class="prose">
    <p><strong>Tilt</strong> is the quarterback share minus the pass-catcher (WR + TE) share.
    Positive means a team's draft value is concentrated under center, negative means it's in
    the receiving corps.</p>
    <p>Read the column knowing that <strong>{n_positive} of {len(rows)} teams is
    positive</strong>. That isn't a fact about these rosters, it's a fact about the format: in
    a {report["teams_in_league_settings"]}-team, one-quarterback league you only need one, so
    quarterbacks slide and carry little draft weight. Tilt ranks teams against each other, not
    against zero.</p>
    <p><strong>{qb_heavy["team"]}</strong> is the one genuinely quarterback-heavy roster
    ({qb_heavy["qb_pct"] * 100:.0f}% QB against
    {qb_heavy["pass_catcher_pct"] * 100:.0f}% pass catchers). At the other end,
    <strong>{pc_heavy["team"]}</strong> puts {pc_heavy["pass_catcher_pct"] * 100:.0f}% into
    receivers and tight ends and {pc_heavy["qb_pct"] * 100:.0f}% at quarterback.</p>
  </div>
  <div class="legend">
    <span><i class="dot seg-qb"></i>QB</span><span><i class="dot seg-rb"></i>RB</span>
    <span><i class="dot seg-wr"></i>WR + TE</span>
    <span style="margin-left:auto">Bars normalized &mdash; shape, not size.</span>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th>Team</th><th>Overall</th><th>Shape of capital</th>
      <th class="n">QB</th><th class="n">RB</th><th class="n">WR+TE</th>
      <th class="n">Tilt</th></tr></thead>
      <tbody>{tilt_rows}</tbody>
    </table>
  </div>
</section>

{offense_sections}

{condensed_section}

{projection_section}

<section>
  <h2>Does the {hl:.0f} matter?</h2>
  <div class="prose">
    <p>It's a knob I chose, so it's worth checking the ordering isn't an artifact of it.
    Re-ranking every team under different settings and correlating against the headline:</p>
  </div>
  <div class="card" style="max-width:520px">
    <div class="scroll"><table>
      <thead><tr><th>Alternative</th><th class="n">Agreement</th><th class="n">Worst move</th></tr></thead>
      <tbody>{sens_rows}</tbody>
    </table></div>
  </div>
  <div class="prose">
    <p>Stable across half lives ({sens["half_life_15"]:.2f} to {sens["half_life_45"]:.2f}), so
    the order reflects the rosters rather than the knob. But stable is not identical: one team
    still moves {max(moves.values())} places between settings, so treat neighbours in the table
    as ties rather than a strict order.</p>
  </div>
</section>

<section>
  <h2>Worked example: {det["team"]}</h2>
  <div class="card">
    <div class="scroll"><table class="players">
      <thead><tr><th>Pos</th><th>Player</th><th class="n">{html.escape(unit)}</th>
      <th class="n">Weight</th></tr></thead>
      <tbody>{_player_rows(det["players"], unit)}</tbody>
    </table></div>
    <p class="caption">Total <strong style="color:var(--ink)">{det["capital"]:.2f}</strong>
    = <strong style="color:var(--ink)">{det["share"] * 100:.1f}%</strong> of the league,
    ranked <strong style="color:var(--ink)">#{det["rank"]}</strong>.</p>
  </div>
</section>

<section>
  <h2>What this doesn't tell you</h2>
  <div class="prose">
    <p><strong>It's draft-market standing, not talent.</strong> A team whose stars are
    underrated by the consensus will look poorer here than it is.</p>
    <p><strong>It rewards depth as well as stars</strong> &mdash; five mid-round players can
    outweigh one elite one. That's a deliberate property of the formula; if you only care
    about top-end talent, read the T24 column instead.</p>
    <p><strong>One snapshot of a moving board.</strong> Rankings shift all through August.</p>
    <p><strong>Kickers and defenses are excluded</strong>, and so is anyone unranked.</p>
  </div>
</section>

<section>
  <h2>Every team, every player</h2>
  <div class="details-grid">{"".join(_team_detail(r, unit) for r in rows)}</div>
</section>

<footer>
  Draft order: {html.escape(report["source"])}, {report["year"]}.
  Offense checks: DraftKings lines via ESPN, and ESPN FPI.
  Generated by <code>fantasyprep.reports.adp_share</code>.
</footer>

</div>

<script>
{scatter_js}
var CURVE = {curve_pts};

function themeColors() {{
  var cs = getComputedStyle(document.documentElement);
  return {{
    accent: cs.getPropertyValue('--wr').trim() || '#35709B',
    rule: cs.getPropertyValue('--rule').trim() || 'rgba(0,0,0,.13)',
    muted: cs.getPropertyValue('--muted').trim() || '#5C6672',
    ink: cs.getPropertyValue('--ink').trim() || '#171C23'
  }};
}}
function fit(cv) {{
  var dpr = window.devicePixelRatio || 1;
  var w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  var g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  return {{ g: g, w: w, h: h }};
}}

function drawCurve() {{
  var cv = document.getElementById('curve');
  if (!cv) return;
  var c = themeColors(), f = fit(cv), g = f.g;
  var padL = 34, padR = 10, padT = 12, padB = 24;
  var iw = f.w - padL - padR, ih = f.h - padT - padB;
  var X = function (a) {{ return padL + (a / 200) * iw; }};
  var Y = function (v) {{ return padT + (1 - v) * ih; }};
  g.strokeStyle = c.rule; g.lineWidth = 1;
  g.font = '11px Archivo, sans-serif'; g.fillStyle = c.muted;
  [0, .25, .5, .75, 1].forEach(function (v) {{
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(f.w - padR, Y(v)); g.stroke();
    g.textAlign = 'right'; g.fillText(v.toFixed(2), padL - 6, Y(v) + 3);
  }});
  g.textAlign = 'center';
  [1, 50, 100, 150, 200].forEach(function (a) {{ g.fillText(a, X(a), f.h - 7); }});
  var grad = g.createLinearGradient(0, padT, 0, padT + ih);
  grad.addColorStop(0, c.accent + '55'); grad.addColorStop(1, c.accent + '00');
  g.beginPath(); g.moveTo(X(CURVE[0][0]), Y(CURVE[0][1]));
  CURVE.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
  g.lineTo(X(CURVE[CURVE.length - 1][0]), Y(0)); g.lineTo(X(CURVE[0][0]), Y(0));
  g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); g.moveTo(X(CURVE[0][0]), Y(CURVE[0][1]));
  CURVE.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
  g.strokeStyle = c.accent; g.lineWidth = 2; g.stroke();
  [[1, 1], [31, .5], [61, .25], [121, .0625]].forEach(function (p) {{
    g.beginPath(); g.arc(X(p[0]), Y(p[1]), 3.5, 0, Math.PI * 2);
    g.fillStyle = c.accent; g.fill();
  }});
}}

function drawScatter(spec) {{
  var cv = document.getElementById(spec.id);
  if (!cv || !spec.data.length) return;
  var c = themeColors(), f = fit(cv), g = f.g, pts = spec.data;
  var padL = 46, padR = 14, padT = 14, padB = 34;
  var iw = f.w - padL - padR, ih = f.h - padT - padB;
  var xs = pts.map(function (d) {{ return d[0]; }});
  var ys = pts.map(function (d) {{ return d[1]; }});
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var xr = x1 - x0 || 1, yr = y1 - y0 || 1;
  x0 -= xr * .08; x1 += xr * .08; y0 -= yr * .12; y1 += yr * .12;
  var X = function (v) {{ return padL + ((v - x0) / (x1 - x0)) * iw; }};
  var Y = function (v) {{ return padT + (1 - (v - y0) / (y1 - y0)) * ih; }};
  g.strokeStyle = c.rule; g.lineWidth = 1;
  g.font = '11px Archivo, sans-serif'; g.fillStyle = c.muted; g.textAlign = 'right';
  for (var i = 0; i <= 4; i++) {{
    var v = y0 + (y1 - y0) * i / 4;
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(f.w - padR, Y(v)); g.stroke();
    g.fillText(v.toFixed(1) + '%', padL - 6, Y(v) + 3);
  }}
  g.textAlign = 'center';
  for (var j = 0; j <= 4; j++) {{
    var xv = x0 + (x1 - x0) * j / 4;
    g.fillText(xv.toFixed(1), X(xv), f.h - 18);
  }}
  g.fillStyle = c.muted; g.font = '11px Archivo, sans-serif';
  g.fillText(spec.x, padL + iw / 2, f.h - 3);
  var n = pts.length;
  var mx = xs.reduce(function (a, b) {{ return a + b; }}, 0) / n;
  var my = ys.reduce(function (a, b) {{ return a + b; }}, 0) / n;
  var num = 0, den = 0;
  for (var k = 0; k < n; k++) {{ num += (xs[k] - mx) * (ys[k] - my); den += Math.pow(xs[k] - mx, 2); }}
  var slope = den ? num / den : 0;
  g.beginPath();
  g.moveTo(X(x0), Y(my + slope * (x0 - mx)));
  g.lineTo(X(x1), Y(my + slope * (x1 - mx)));
  g.strokeStyle = c.muted; g.setLineDash([5, 4]); g.lineWidth = 1.2; g.stroke();
  g.setLineDash([]);
  pts.forEach(function (d) {{
    var px = X(d[0]), py = Y(d[1]);
    var resid = d[1] - (my + slope * (d[0] - mx));
    var big = Math.abs(resid) > (y1 - y0) * .07;
    g.beginPath(); g.arc(px, py, 4.5, 0, Math.PI * 2);
    g.fillStyle = c.accent; g.globalAlpha = .85; g.fill(); g.globalAlpha = 1;
    g.fillStyle = big ? c.ink : c.muted;
    g.font = (big ? '600 ' : '') + '10px Archivo, sans-serif';
    g.textAlign = 'center';
    g.fillText(d[2], px, py - 8);
  }});
}}

function drawQuadrant() {{
  var cv = document.getElementById('quadrant');
  if (!cv || !QUADRANT) return;
  var c = themeColors(), f = fit(cv), g = f.g;
  var pts = QUADRANT.points;
  var padL = 46, padR = 16, padT = 16, padB = 40;
  var iw = f.w - padL - padR, ih = f.h - padT - padB;
  var xs = pts.map(function (d) {{ return d[0]; }});
  var ys = pts.map(function (d) {{ return d[1]; }});
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var xr = x1 - x0 || 1, yr = y1 - y0 || 1;
  x0 -= xr * .10; x1 += xr * .10; y0 -= yr * .12; y1 += yr * .12;
  // x reversed: fewer effective players (more condensed) sits to the RIGHT,
  // so the desirable corner is up-and-right, which reads as "good" by default.
  var X = function (v) {{ return padL + (1 - (v - x0) / (x1 - x0)) * iw; }};
  var Y = function (v) {{ return padT + (1 - (v - y0) / (y1 - y0)) * ih; }};

  var mx = X(QUADRANT.mx), my = Y(QUADRANT.my);
  g.fillStyle = c.accent; g.globalAlpha = .09;
  g.fillRect(mx, padT, (padL + iw) - mx, my - padT);
  g.globalAlpha = 1;

  g.strokeStyle = c.rule; g.lineWidth = 1;
  g.font = '11px Archivo, sans-serif'; g.fillStyle = c.muted; g.textAlign = 'right';
  for (var i = 0; i <= 4; i++) {{
    var v = y0 + (y1 - y0) * i / 4;
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(padL + iw, Y(v)); g.stroke();
    g.fillText(v.toFixed(0), padL - 6, Y(v) + 3);
  }}
  g.textAlign = 'center';
  for (var j = 0; j <= 4; j++) {{
    var xv = x0 + (x1 - x0) * j / 4;
    g.fillText(xv.toFixed(1), X(xv), f.h - 22);
  }}
  g.fillText('effective drafted players  (fewer →)', padL + iw / 2, f.h - 6);
  g.save(); g.translate(12, padT + ih / 2); g.rotate(-Math.PI / 2);
  g.textAlign = 'center'; g.fillText('implied points per game', 0, 0); g.restore();

  g.setLineDash([4, 4]); g.strokeStyle = c.muted; g.globalAlpha = .55;
  g.beginPath(); g.moveTo(mx, padT); g.lineTo(mx, padT + ih); g.stroke();
  g.beginPath(); g.moveTo(padL, my); g.lineTo(padL + iw, my); g.stroke();
  g.globalAlpha = 1; g.setLineDash([]);

  g.font = '600 10px Archivo, sans-serif'; g.fillStyle = c.accent; g.textAlign = 'right';
  g.fillText('CONDENSED SCORING', padL + iw - 6, padT + 14);

  pts.forEach(function (d) {{
    var px = X(d[0]), py = Y(d[1]), hot = d[3];
    g.beginPath(); g.arc(px, py, hot ? 6 : 4, 0, Math.PI * 2);
    g.fillStyle = c.accent; g.globalAlpha = hot ? 1 : .45; g.fill(); g.globalAlpha = 1;
    g.fillStyle = hot ? c.ink : c.muted;
    g.font = (hot ? '600 ' : '') + '10px Archivo, sans-serif';
    g.textAlign = 'center';
    g.fillText(d[2], px, py - 9);
  }});
}}

function drawAll() {{
  drawCurve();
  drawQuadrant();
  SCATTERS.forEach(drawScatter);
}}
drawAll();
window.addEventListener('resize', drawAll);
if (window.matchMedia) {{
  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(drawAll);
}}
new MutationObserver(drawAll).observe(document.documentElement, {{
  attributes: true, attributeFilter: ['data-theme']
}});
</script>
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", type=Path, default=Path("data/adp_share_2026.json"))
    parser.add_argument("--offense", type=Path, default=Path("data/offense_rankings_2026.json"))
    parser.add_argument("--condensed", type=Path, default=Path("data/condensed_offense_2026.json"))
    parser.add_argument("--projection", type=Path, default=Path("data/vegas_projection_2026.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = json.loads(args.src.read_text(encoding="utf-8"))
    offense = (
        json.loads(args.offense.read_text(encoding="utf-8"))
        if args.offense and args.offense.exists()
        else None
    )
    condensed = (
        json.loads(args.condensed.read_text(encoding="utf-8"))
        if args.condensed and args.condensed.exists()
        else None
    )
    projection = (
        json.loads(args.projection.read_text(encoding="utf-8"))
        if args.projection and args.projection.exists()
        else None
    )
    args.out.write_text(render(report, offense, condensed, projection), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
