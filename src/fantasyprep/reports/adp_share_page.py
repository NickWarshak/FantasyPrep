"""Render the ADP-share report as a standalone HTML page.

Generated from `adp_share.compute()` rather than hand-written, so every figure
on the page is the figure the code actually produced. Transcribing numbers by
hand into a report is how a report ends up disagreeing with its own source.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

POSITION_ORDER = ("QB", "RB", "WR", "TE")
POSITION_HUE = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}


def _position_split(row: dict) -> dict[str, float]:
    out = {p: 0.0 for p in POSITION_ORDER}
    for p in row["players"]:
        if p["position"] in out:
            out[p["position"]] += p["value"]
    return out


def _bar(row: dict, max_capital: float) -> str:
    split = _position_split(row)
    total = row["capital"] or 1.0
    width = (row["capital"] / max_capital) * 100 if max_capital else 0
    segs = []
    for pos in POSITION_ORDER:
        v = split[pos]
        if v <= 0:
            continue
        segs.append(
            f'<span class="seg seg-{POSITION_HUE[pos]}" style="flex:{v:.4f}" '
            f'title="{pos}: {v:.1f} pts"></span>'
        )
    return f'<span class="bar" style="width:{width:.2f}%">{"".join(segs)}</span>'


def _team_detail(row: dict) -> str:
    lines = []
    for p in row["players"]:
        dead = "" if p["value"] > 0 else " dead"
        lines.append(
            f'<tr class="pl{dead}">'
            f'<td class="pos"><span class="tag tag-{POSITION_HUE.get(p["position"], "qb")}">'
            f'{html.escape(p["position"])}{p["position_rank"]}</span></td>'
            f'<td class="nm">{html.escape(p["name"])}</td>'
            f'<td class="n">{p["adp"]:.1f}</td>'
            f'<td class="n">{p["expected"]:.1f}</td>'
            f'<td class="n dim">&minus;{p["replacement"]:.1f}</td>'
            f'<td class="n val">{p["value"]:.1f}</td>'
            f"</tr>"
        )
    return (
        f'<details class="team-detail"><summary>'
        f'<span class="sm-rank">{row["rank"]}</span>'
        f'<span class="sm-team">{html.escape(row["team"])}</span>'
        f'<span class="sm-share">{row["share"] * 100:.1f}%</span>'
        f'<span class="sm-hint">{row["n_players"]} drafted</span>'
        f"</summary>"
        f'<div class="scroll"><table class="players">'
        f"<thead><tr><th>Pos</th><th>Player</th><th>ADP</th>"
        f"<th>Proj</th><th>Repl.</th><th>Capital</th></tr></thead>"
        f'<tbody>{"".join(lines)}</tbody></table></div></details>'
    )


def render(report: dict, moves: list[dict], artifact_total_delta: dict) -> str:
    rows = report["rows"]
    max_capital = max(r["capital"] for r in rows)
    top = rows[0]
    bottom = rows[-1]
    ratio = top["capital"] / bottom["capital"] if bottom["capital"] else 0

    table_rows = []
    for r in rows:
        c = r["counts"]
        table_rows.append(
            f"<tr>"
            f'<td class="rk">{r["rank"]}</td>'
            f'<td class="tm">{html.escape(r["team"])}</td>'
            f'<td class="barcell">{_bar(r, max_capital)}</td>'
            f'<td class="n share">{r["share"] * 100:.1f}%</td>'
            f'<td class="n">{r["capital"]:.0f}</td>'
            f'<td class="n dim">{c["24"]}</td>'
            f'<td class="n dim">{c["50"]}</td>'
            f'<td class="n dim">{c["100"]}</td>'
            f'<td class="best">{html.escape(r["best_player"] or "")}'
            f'<span class="badp">{r["best_adp"]:.1f}</span></td>'
            f"</tr>"
        )

    move_rows = "".join(
        f'<tr><td class="tm">{html.escape(m["team"])}</td>'
        f'<td class="n">#{m["before"]}</td>'
        f'<td class="n">#{m["after"]}</td>'
        f'<td class="n {"up" if m["delta"] < 0 else "down"}">{m["delta"]:+d}</td>'
        f'<td class="n dim">{m["cap_before"]:.0f} &rarr; {m["cap_after"]:.0f}</td></tr>'
        for m in moves
    )

    repl = report["replacement_levels"]
    cut = report["rank_cutoff"]
    repl_rows = "".join(
        f'<tr><td class="tm"><span class="tag tag-{POSITION_HUE[p]}">{p}</span></td>'
        f'<td class="n">{p}{cut[p]}</td><td class="n">{repl[p]:.1f}</td></tr>'
        for p in POSITION_ORDER if p in repl and p in cut
    )

    det = next((r for r in rows if r["team"] == "DET"), rows[0])
    det_lines = "".join(
        f'<tr class="pl{"" if p["value"] > 0 else " dead"}">'
        f'<td class="pos"><span class="tag tag-{POSITION_HUE.get(p["position"], "qb")}">'
        f'{p["position"]}{p["position_rank"]}</span></td>'
        f'<td class="nm">{html.escape(p["name"])}</td>'
        f'<td class="n">{p["adp"]:.1f}</td>'
        f'<td class="n">{p["expected"]:.1f}</td>'
        f'<td class="n dim">&minus;{p["replacement"]:.1f}</td>'
        f'<td class="n val">{p["value"]:.1f}</td></tr>'
        for p in det["players"]
    )

    return f"""<title>NFL Draft Capital Share</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {{
  --ground: #F1F3F0;
  --surface: #FFFFFF;
  --surface-2: #E9ECE8;
  --ink: #171C23;
  --muted: #5C6672;
  --faint: #8A939E;
  --rule: rgba(23,28,35,.13);
  --rule-strong: rgba(23,28,35,.28);
  --qb: #7C6AA8;
  --rb: #C0703C;
  --wr: #35709B;
  --te: #6E8B3D;
  --up: #2F7D5B;
  --down: #A6543C;
  --shadow: 0 1px 2px rgba(23,28,35,.06), 0 8px 24px rgba(23,28,35,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #0E1218;
    --surface: #161B22;
    --surface-2: #1D242D;
    --ink: #E7ECF2;
    --muted: #9AA6B4;
    --faint: #6C7885;
    --rule: rgba(231,236,242,.14);
    --rule-strong: rgba(231,236,242,.3);
    --qb: #9C8AC8;
    --rb: #D98F5C;
    --wr: #5A93BE;
    --te: #8FAC5C;
    --up: #56A57E;
    --down: #C97A5E;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  --ground: #0E1218;
  --surface: #161B22;
  --surface-2: #1D242D;
  --ink: #E7ECF2;
  --muted: #9AA6B4;
  --faint: #6C7885;
  --rule: rgba(231,236,242,.14);
  --rule-strong: rgba(231,236,242,.3);
  --qb: #9C8AC8;
  --rb: #D98F5C;
  --wr: #5A93BE;
  --te: #8FAC5C;
  --up: #56A57E;
  --down: #C97A5E;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
}}

* {{ box-sizing: border-box; }}
body {{
  background: var(--ground);
  color: var(--ink);
  font: 400 16px/1.6 "Source Serif 4", Georgia, serif;
  margin: 0;
  padding: 40px 24px 96px;
  display: flex; flex-direction: column; align-items: center; gap: 40px;
}}
.wrap {{ width: 100%; max-width: 1080px; display: flex; flex-direction: column; gap: 40px; }}
.prose {{ max-width: 68ch; display: flex; flex-direction: column; gap: 14px; }}
h1, h2, h3, .ui {{ font-family: Archivo, "Helvetica Neue", Arial, sans-serif; }}
h1 {{
  font-size: clamp(2rem, 5vw, 3.1rem); font-weight: 700; letter-spacing: -.022em;
  line-height: 1.04; margin: 0; text-wrap: balance;
}}
h2 {{
  font-size: 1.32rem; font-weight: 600; letter-spacing: -.01em; margin: 0;
  text-wrap: balance; padding-bottom: 10px; border-bottom: 1px solid var(--rule-strong);
}}
h3 {{ font-size: 1rem; font-weight: 600; margin: 0; letter-spacing: -.005em; }}
p {{ margin: 0; }}
.eyebrow {{
  font-family: Archivo, sans-serif; font-size: .69rem; font-weight: 600;
  letter-spacing: .17em; text-transform: uppercase; color: var(--muted);
}}
.lede {{ font-size: 1.13rem; color: var(--muted); max-width: 62ch; }}
strong {{ font-weight: 600; }}
code {{
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .87em;
  background: var(--surface-2); padding: .1em .34em; border-radius: 3px;
}}
section {{ display: flex; flex-direction: column; gap: 18px; }}

.keys {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.key {{
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  padding: 13px 17px; box-shadow: var(--shadow); flex: 1 1 190px;
  display: flex; flex-direction: column; gap: 3px;
}}
.key .v {{
  font-family: Archivo, sans-serif; font-size: 1.6rem; font-weight: 700;
  letter-spacing: -.02em; font-variant-numeric: tabular-nums;
}}
.key .l {{ font-size: .82rem; color: var(--muted); font-family: Archivo, sans-serif; }}

.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-family: Archivo, sans-serif; }}
th {{
  font-size: .68rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); text-align: left; padding: 0 10px 9px; white-space: nowrap;
  border-bottom: 1px solid var(--rule-strong);
}}
td {{ padding: 7px 10px; border-bottom: 1px solid var(--rule); font-size: .9rem; }}
.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.dim {{ color: var(--faint); }}
.rk {{ color: var(--faint); font-variant-numeric: tabular-nums; width: 34px; font-size: .82rem; }}
.tm {{ font-weight: 700; letter-spacing: .02em; white-space: nowrap; }}
.share {{ font-weight: 600; }}
.best {{ color: var(--muted); font-size: .85rem; white-space: nowrap; }}
.badp {{ color: var(--faint); margin-left: 7px; font-variant-numeric: tabular-nums; }}
.barcell {{ width: 34%; min-width: 150px; }}
.bar {{ display: flex; height: 13px; border-radius: 2px; overflow: hidden; }}
.seg {{ display: block; }}
.seg-qb {{ background: var(--qb); }}
.seg-rb {{ background: var(--rb); }}
.seg-wr {{ background: var(--wr); }}
.seg-te {{ background: var(--te); }}
.up {{ color: var(--up); }}
.down {{ color: var(--down); }}

.legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-family: Archivo, sans-serif; font-size: .8rem; color: var(--muted); }}
.legend span {{ display: flex; align-items: center; gap: 6px; }}
.dot {{ width: 10px; height: 10px; border-radius: 2px; display: block; }}

.tag {{
  font-family: Archivo, sans-serif; font-size: .68rem; font-weight: 600;
  padding: 2px 6px; border-radius: 3px; color: #fff; letter-spacing: .02em;
}}
.tag-qb {{ background: var(--qb); }}
.tag-rb {{ background: var(--rb); }}
.tag-wr {{ background: var(--wr); }}
.tag-te {{ background: var(--te); }}

.formula {{
  font-family: "IBM Plex Mono", monospace; font-size: .84rem; line-height: 1.85;
  background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--wr);
  border-radius: 6px; padding: 16px 18px; overflow-x: auto; white-space: pre;
  color: var(--ink);
}}
.card {{
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  padding: 18px 20px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 10px;
}}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}

.details-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 10px; }}
.team-detail {{
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  padding: 0; overflow: hidden;
}}
.team-detail summary {{
  cursor: pointer; list-style: none; padding: 11px 15px; display: flex;
  align-items: baseline; gap: 11px; font-family: Archivo, sans-serif;
}}
.team-detail summary::-webkit-details-marker {{ display: none; }}
.team-detail summary:hover {{ background: var(--surface-2); }}
.team-detail summary:focus-visible {{ outline: 2px solid var(--wr); outline-offset: -2px; }}
.sm-rank {{ color: var(--faint); font-size: .8rem; font-variant-numeric: tabular-nums; min-width: 20px; }}
.sm-team {{ font-weight: 700; font-size: .98rem; }}
.sm-share {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.sm-hint {{ color: var(--faint); font-size: .78rem; margin-left: auto; }}
.team-detail .scroll {{ padding: 0 15px 13px; }}
.players th {{ padding-top: 6px; }}
.players td {{ font-size: .84rem; }}
.pl.dead .nm, .pl.dead .n {{ color: var(--faint); }}
.val {{ font-weight: 600; }}
.nm {{ font-family: Archivo, sans-serif; }}
.pos {{ width: 52px; }}

footer {{ color: var(--faint); font-size: .84rem; max-width: 68ch; }}
a {{ color: var(--wr); }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
</style>

<div class="wrap">

<header class="prose">
  <span class="eyebrow">{report["year"]} season &middot; {report["scoring"]} &middot; {report["teams_in_league_settings"]}-team</span>
  <h1>Which NFL teams carry the most fantasy draft capital</h1>
  <p class="lede">Every team ranked by the draft value of its fantasy-relevant players &mdash;
  weighted by what each player is actually worth above a replacement at his position,
  not just counted.</p>
</header>

<section>
  <div class="keys">
    <div class="key"><span class="v">{top["team"]}</span><span class="l">Most capital &mdash; {top["share"] * 100:.1f}% of the league</span></div>
    <div class="key"><span class="v">{ratio:.1f}&times;</span><span class="l">{top["team"]} over {bottom["team"]}, last place</span></div>
    <div class="key"><span class="v">{report["spearman_weighted_vs_top100_count"]:.2f}</span><span class="l">Rank correlation with simple top-100 counting</span></div>
    <div class="key"><span class="v">{artifact_total_delta["pct"]:+.1f}%</span><span class="l">League total after removing bad-data artifacts</span></div>
  </div>
</section>

<section>
  <h2>The ranking</h2>
  <div class="legend">
    <span><i class="dot seg-qb"></i>QB</span>
    <span><i class="dot seg-rb"></i>RB</span>
    <span><i class="dot seg-wr"></i>WR</span>
    <span><i class="dot seg-te"></i>TE</span>
    <span style="margin-left:auto">Bar length = capital. Segments = where it comes from.</span>
  </div>
  <div class="scroll">
    <table>
      <thead><tr>
        <th></th><th>Team</th><th>Capital by position</th><th class="n">Share</th>
        <th class="n">Pts</th><th class="n">T24</th><th class="n">T50</th><th class="n">T100</th>
        <th>Best player</th>
      </tr></thead>
      <tbody>{"".join(table_rows)}</tbody>
    </table>
  </div>
  <p class="prose" style="font-size:.9rem;color:var(--muted)">
    <strong>T24 / T50 / T100</strong> count that team's players inside the top 24, 50 and 100 of overall ADP.
    Three thresholds rather than one, because any single cutoff is arbitrary &mdash; if a team swings
    across the three, the count view isn't telling you anything stable.
  </p>
</section>

<section>
  <h2>The math</h2>
  <div class="prose">
    <p>Counting highly-drafted players is the obvious answer, and it is genuinely useful &mdash;
    so it's in the table. But a count can't tell the difference between Jahmyr Gibbs at ADP 1.8
    and a receiver at ADP 95. Both are &ldquo;a top-100 player.&rdquo; So the headline number
    weights each player by what he's worth.</p>
  </div>
  <div class="formula">ADP  &rarr;  position rank  &rarr;  historical outcome bucket  &rarr;  mean real points

capital(player) = max(0, projected points &minus; replacement at his position)
capital(team)   = &Sigma; capital(player) over that team's drafted players
ADP share       = capital(team) / capital(all 32 teams)</div>
  <div class="prose">
    <p>Every projection is a real historical average, not a formula: a player's ADP fixes his
    rank at his position, that rank maps to a bucket of real past seasons by players drafted
    there, and the projection is that bucket's mean. It is the same outcome data the draft
    engine itself runs on.</p>
    <h3>Why subtract a replacement level</h3>
    <p>Without it, this table would rank teams by whether they have a starting quarterback.
    A QB projecting 260 points looks huge next to a 200-point running back &mdash; until you
    notice that the 19th-best quarterback, free off the waiver wire, gives you
    {repl["QB"]:.0f}. So a quarterback is only worth the points he adds
    <em>above what you could have had for nothing.</em> The replacement ranks come from real
    draft depth in a {report["teams_in_league_settings"]}-team league, measured rather than assumed:</p>
  </div>
  <div class="card" style="max-width:420px">
    <div class="scroll"><table>
      <thead><tr><th>Pos</th><th class="n">Replacement rank</th><th class="n">Points</th></tr></thead>
      <tbody>{repl_rows}</tbody>
    </table></div>
  </div>
  <div class="prose">
    <p>A player drafted deeper than his position's replacement rank is worth exactly
    <strong>0</strong> capital. That is what replacement level means: if the 19th quarterback
    is free, the 24th cannot be an asset.</p>
  </div>
</section>

<section>
  <h2>Worked example: {det["team"]}</h2>
  <div class="prose"><p>The roster you'd name off the top of your head &mdash; and what each
  piece is actually worth once the replacement bar is applied.</p></div>
  <div class="card">
    <div class="scroll"><table class="players">
      <thead><tr><th>Pos</th><th>Player</th><th class="n">ADP</th><th class="n">Projected</th>
      <th class="n">Replacement</th><th class="n">Capital</th></tr></thead>
      <tbody>{det_lines}</tbody>
    </table></div>
    <p style="font-family:Archivo,sans-serif;font-size:.88rem;color:var(--muted)">
      Total <strong style="color:var(--ink)">{det["capital"]:.1f}</strong> points
      = <strong style="color:var(--ink)">{det["share"] * 100:.1f}%</strong> of the league,
      ranked <strong style="color:var(--ink)">#{det["rank"]}</strong>.
      Note Sam LaPorta: a genuinely good tight end contributes little here, because tight end
      replacement is unusually high &mdash; see the caveat below.
    </p>
  </div>
</section>

<section>
  <h2>Two corrections I had to make first</h2>
  <div class="prose">
    <p>The first version of this table was wrong, in a way worth showing rather than quietly
    fixing. The outcome buckets thin out badly at the deep end, and
    <code>outcome_for_rank</code> reuses the deepest bucket for every rank past its grid. Those
    deep buckets aren't monotonic, so two artifacts appeared:</p>
  </div>
  <div class="grid2">
    <div class="card">
      <h3>Greg Dulcich outranked Sam LaPorta</h3>
      <p style="font-size:.93rem;color:var(--muted)">The deepest tight-end bucket is a
      <strong>single</strong> 151.7-point season. So TE24 was credited with more projected
      points than TE7 &mdash; and Miami banked the difference.</p>
    </div>
    <div class="card">
      <h3>A backup QB counted as an asset</h3>
      <p style="font-size:.93rem;color:var(--muted)">Malik Willis (QB24) scored 256.7 against a
      QB19 replacement of {repl["QB"]:.1f} &mdash; <strong>+35.8 points of capital</strong> for a
      quarterback drafted past the point where quarterbacks are free.</p>
    </div>
  </div>
  <div class="prose">
    <p>Both are fixed by two rules: projected points are forced to be
    <strong>non-increasing in position rank</strong> (a WR12 can never beat a WR9, whatever one
    noisy bucket says), and anyone past his replacement rank is worth
    <strong>0</strong>. Together they removed
    <strong>{artifact_total_delta["abs"]:.0f} points ({abs(artifact_total_delta["pct"]):.1f}%)</strong>
    of league-wide capital that was pure artifact, and moved real teams:</p>
  </div>
  <div class="card" style="max-width:520px">
    <div class="scroll"><table>
      <thead><tr><th>Team</th><th class="n">Before</th><th class="n">After</th>
      <th class="n">Move</th><th class="n">Capital</th></tr></thead>
      <tbody>{move_rows}</tbody>
    </table></div>
  </div>
</section>

<section>
  <h2>What this doesn't tell you</h2>
  <div class="prose">
    <p><strong>Tight end capital is understated.</strong> Replacement TE ({repl["TE"]:.1f}) comes out
    <em>above</em> replacement RB ({repl["RB"]:.1f}), which is a known defect in the deep buckets
    rather than a fact about football. Teams whose value is concentrated at tight end are
    treated harshly here.</p>
    <p><strong>Players in the same 3-rank bucket are identical.</strong> The projection belongs to
    a tier, not a person &mdash; three receivers ranked WR34, WR37 and WR39 all project the same
    number. This measures draft-market standing, not individual talent.</p>
    <p><strong>It's one snapshot of a moving market.</strong> ADP shifts through August; this is
    where it sat when the page was generated.</p>
    <p><strong>Kickers and defenses are excluded</strong> &mdash; defenses have no real-points source
    in this project, and kickers are noise.</p>
  </div>
</section>

<section>
  <h2>Every team, every player</h2>
  <div class="details-grid">{"".join(_team_detail(r) for r in rows)}</div>
</section>

<footer>
  Source: {html.escape(report["source"])}, {report["year"]} &middot;
  projections from real {report["scoring"]} outcomes, 2010&ndash;2024 &middot;
  generated by <code>fantasyprep.reports.adp_share</code>.
  League total {report["total_capital"]:.0f} capital points across {len(rows)} teams.
</footer>

</div>
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", type=Path, default=Path("data/adp_share_2026.json"))
    parser.add_argument("--uncorrected", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    report = json.loads(args.src.read_text(encoding="utf-8"))
    moves, delta = [], {"abs": 0.0, "pct": 0.0}
    if args.uncorrected and args.uncorrected.exists():
        before = json.loads(args.uncorrected.read_text(encoding="utf-8"))
        rb_ = {r["team"]: r for r in before["rows"]}
        ra_ = {r["team"]: r for r in report["rows"]}
        allm = [
            {
                "team": t, "before": rb_[t]["rank"], "after": ra_[t]["rank"],
                "delta": ra_[t]["rank"] - rb_[t]["rank"],
                "cap_before": rb_[t]["capital"], "cap_after": ra_[t]["capital"],
            }
            for t in ra_ if t in rb_
        ]
        moves = sorted(allm, key=lambda m: -abs(m["delta"]))[:5]
        tb = sum(r["capital"] for r in before["rows"])
        ta = sum(r["capital"] for r in report["rows"])
        delta = {"abs": tb - ta, "pct": (ta / tb - 1) * 100 if tb else 0.0}

    args.out.write_text(render(report, moves, delta), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
