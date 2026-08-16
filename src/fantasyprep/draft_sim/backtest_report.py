"""Renders backtest.py's results (a JSON dump of ReplayResult rows) into a
self-contained HTML report. Same visual system as
historical/report_render.py's Data Integrity Review -- same tokens, same
stat-tile/data-table/facet patterns -- so the project's reports read as one
consistent series rather than one-offs.

Usage:
    python -m fantasyprep.draft_sim.backtest_report --in data/backtest_results.json --out data/backtest_report.html
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
from pathlib import Path

from fantasyprep.draft_sim.backtest import ReplayResult, _win_rate_stat, cluster_bootstrap_ci
from fantasyprep.draft_sim.backtest_analysis import position_breakdown
from fantasyprep.league.settings import default_settings

POSITION_COLOR = {
    "QB": ("#2a78d6", "#3987e5"),
    "RB": ("#eb6834", "#d95926"),
    "WR": ("#1baf7a", "#199e70"),
    "TE": ("#eda100", "#c98500"),
    "DST": ("#8a6fd1", "#a48ce8"),
}


def _esc(s) -> str:
    return html.escape(str(s))


def _percentile(sorted_values: list[float], pct: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, int(pct * len(sorted_values))))
    return sorted_values[idx]


def _to_replay_results(results: list[dict]) -> list[ReplayResult]:
    return [
        ReplayResult(
            year=r["year"], my_slot=r["my_slot"], seed_index=r.get("seed_index", 0),
            baseline_points=r["baseline_points"], model_points=r["model_points"],
            pure_adp_points=r.get("pure_adp_points", r["baseline_points"]),
            vor_points=r.get("vor_points", r["baseline_points"]),
            baseline_roster=r["baseline_roster"], model_roster=r["model_roster"],
            pure_adp_roster=r.get("pure_adp_roster", r["baseline_roster"]),
            vor_roster=r.get("vor_roster", r["baseline_roster"]),
        )
        for r in results
    ]


def _has_pure_adp(results: list[dict]) -> bool:
    """Older result files (pre pure-ADP-chalk addition) won't have this key."""
    return bool(results) and "pure_adp_points" in results[0]


def _has_vor(results: list[dict]) -> bool:
    """Older result files (pre VOR-as-4th-condition addition) won't have this key."""
    return bool(results) and "vor_points" in results[0]


def _summary_for(results: list[dict], key: str) -> dict:
    """`key` is 'delta' (vs ADP+need baseline), 'delta_vs_pure_adp', or 'delta_vs_vor'."""
    deltas = sorted(r[key] for r in results)
    n = len(deltas)
    wins = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    ties = n - wins - losses

    replay_results = _to_replay_results(results)
    value_fns = {
        "delta": lambda r: r.delta,
        "delta_vs_pure_adp": lambda r: r.delta_vs_pure_adp,
        "delta_vs_vor": lambda r: r.delta_vs_vor,
    }
    value_fn = value_fns[key]
    win_lo, win_hi = cluster_bootstrap_ci(replay_results, _win_rate_stat, seed=0, value_fn=value_fn)
    mean_lo, mean_hi = cluster_bootstrap_ci(replay_results, statistics.mean, seed=1, value_fn=value_fn)
    median_lo, median_hi = cluster_bootstrap_ci(replay_results, statistics.median, seed=2, value_fn=value_fn)

    return {
        "n": n, "wins": wins, "losses": losses, "ties": ties,
        "win_rate": wins / n, "win_rate_ci": (win_lo, win_hi),
        "mean": statistics.mean(deltas), "mean_ci": (mean_lo, mean_hi),
        "median": statistics.median(deltas), "median_ci": (median_lo, median_hi),
        "min": deltas[0], "p10": _percentile(deltas, 0.1),
        "p90": _percentile(deltas, 0.9), "max": deltas[-1],
        "max_abs": max(abs(d) for d in deltas) or 1.0,
        "n_clusters": len({(r["year"], r["my_slot"]) for r in results}),
        "n_seasons": len({r["year"] for r in results}),
    }


def _summary(results: list[dict]) -> dict:
    return _summary_for(results, "delta")


def _per_year(results: list[dict], key: str = "delta") -> list[dict]:
    years = sorted({r["year"] for r in results})
    rows = []
    for year in years:
        year_results = [r for r in results if r["year"] == year]
        rows.append({"year": year, **_summary_for(year_results, key)})
    return rows


def _by_cluster(results: list[dict], key: str = "delta") -> list[dict]:
    """One row per (year, slot), aggregating however many opponent-room
    seeds it has -- reporting clustered rather than pretending every seed
    is an independent observation (matters a lot once num_seeds > 1)."""
    clusters: dict[tuple[int, int], list[dict]] = {}
    for r in results:
        clusters.setdefault((r["year"], r["my_slot"]), []).append(r)

    rows = []
    for (year, slot), members in clusters.items():
        deltas = [m[key] for m in members]
        rows.append({
            "year": year, "my_slot": slot, "n_seeds": len(members),
            "mean": statistics.mean(deltas), "min": min(deltas), "max": max(deltas),
        })
    rows.sort(key=lambda r: r["mean"], reverse=True)
    return rows


def _roster_diff(baseline_roster: list, model_roster: list) -> tuple[list, list]:
    base_set = {(n, p) for n, p, _pts in baseline_roster}
    model_set = {(n, p) for n, p, _pts in model_roster}
    only_base = sorted(base_set - model_set)
    only_model = sorted(model_set - base_set)
    return only_base, only_model


def gather_report_data(results: list[dict]) -> dict:
    swings = sorted(results, key=lambda r: abs(r["delta"]), reverse=True)[:6]
    cluster_rows = _by_cluster(results)
    max_abs_cluster = max((max(abs(r["min"]), abs(r["max"])) for r in cluster_rows), default=1.0) or 1.0
    has_pure_adp = _has_pure_adp(results)
    has_vor = _has_vor(results)

    data = {
        "summary": _summary(results),
        "per_year": _per_year(results),
        "clusters": cluster_rows,
        "max_abs_cluster": max_abs_cluster,
        "swings": swings,
        "years": sorted({r["year"] for r in results}),
        "slots": sorted({r["my_slot"] for r in results}),
        "has_pure_adp": has_pure_adp,
        "has_vor": has_vor,
    }

    if has_pure_adp:
        pure_adp_clusters = _by_cluster(results, key="delta_vs_pure_adp")
        data["summary_vs_pure_adp"] = _summary_for(results, "delta_vs_pure_adp")
        data["clusters_vs_pure_adp"] = pure_adp_clusters
        data["max_abs_cluster_vs_pure_adp"] = (
            max((max(abs(r["min"]), abs(r["max"])) for r in pure_adp_clusters), default=1.0) or 1.0
        )

    if has_vor:
        vor_clusters = _by_cluster(results, key="delta_vs_vor")
        data["summary_vs_vor"] = _summary_for(results, "delta_vs_vor")
        data["clusters_vs_vor"] = vor_clusters
        data["max_abs_cluster_vs_vor"] = (
            max((max(abs(r["min"]), abs(r["max"])) for r in vor_clusters), default=1.0) or 1.0
        )
        data["position_breakdown_vs_vor"] = position_breakdown(results, default_settings(), "vor_roster")

    if results and "confidence_weighted_points" in results[0]:
        weighted_total = sum(r["confidence_weighted_points"] for r in results)
        actual_total = sum(r["confidence_weighted_actual_points"] for r in results)
        data["calibration"] = {
            "weighted_total": weighted_total,
            "actual_total": actual_total,
            "gap": weighted_total - actual_total,
        }

    if results:
        data["position_breakdown"] = position_breakdown(results, default_settings())

    return data


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _stat_tile(label: str, value: str, status: str | None = None, sub: str | None = None) -> str:
    cls = f"stat-tile status-{status}" if status else "stat-tile"
    sub_html = f'<div class="stat-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="{cls}"><div class="stat-value">{value}</div>'
        f'<div class="stat-label">{_esc(label)}</div>{sub_html}</div>'
    )


def _pct_pos(value: float, max_abs: float) -> float:
    """0-100 position on a track where 50 = zero, scaled to +/-max_abs."""
    return 50.0 + max(-50.0, min(50.0, (value / max_abs) * 50.0))


def _delta_chart(clusters: list[dict], max_abs: float) -> str:
    """One row per (year, slot): a range bar spanning that cell's
    min-to-max delta across its opponent-room seeds, with the mean marked.
    A single-seed cell degenerates to a single point, which is exactly
    what it should show -- no manufactured uncertainty where there isn't
    any yet."""
    rows = []
    for r in clusters:
        left_pct = _pct_pos(r["min"], max_abs)
        right_pct = _pct_pos(r["max"], max_abs)
        mean_pct = _pct_pos(r["mean"], max_abs)
        range_cls = "positive" if r["mean"] > 0 else ("negative" if r["mean"] < 0 else "tie")
        seed_note = f' &times;{r["n_seeds"]}' if r["n_seeds"] > 1 else ""
        rows.append(
            f'<div class="delta-row">'
            f'<div class="delta-label">{r["year"]} &middot; slot {r["my_slot"]}{seed_note}</div>'
            f'<div class="delta-track">'
            f'<div class="delta-zero"></div>'
            f'<div class="delta-range {range_cls}" style="left:{min(left_pct, right_pct):.1f}%;width:{abs(right_pct-left_pct):.1f}%"></div>'
            f'<div class="delta-mean {range_cls}" style="left:{mean_pct:.1f}%"></div>'
            f'</div>'
            f'<div class="delta-value {range_cls}">{r["mean"]:+.1f}</div>'
            f'</div>'
        )
    return f'<div class="delta-chart">{"".join(rows)}</div>'

def _per_year_tiles(per_year: list[dict]) -> str:
    cards = []
    for row in per_year:
        status = "good" if row["mean"] > 0 else ("critical" if row["mean"] < 0 else None)
        cards.append(f'''
        <div class="year-card">
          <div class="year-card-title">{row["year"]}</div>
          <div class="year-card-record">{row["wins"]}-{row["losses"]}-{row["ties"]}</div>
          <div class="year-card-mean stat-{status or "neutral"}">{row["mean"]:+.1f} avg</div>
        </div>''')
    return f'<div class="year-grid">{"".join(cards)}</div>'


def _position_pill(name: str, position: str) -> str:
    cl, cd = POSITION_COLOR.get(position, ("#8993A4", "#7C8797"))
    return f'<span class="pill" style="--series:{cl};--series-dark:{cd}">{_esc(name)} <span class="pill-pos">{position}</span></span>'


def _swing_cards(swings: list[dict]) -> str:
    cards = []
    for r in swings:
        only_base, only_model = _roster_diff(r["baseline_roster"], r["model_roster"])
        won = r["delta"] > 0
        cards.append(f'''
        <div class="swing-card">
          <div class="swing-head">
            <span class="swing-title">{r["year"]} &middot; slot {r["my_slot"]}</span>
            <span class="swing-delta {"positive" if won else "negative"}">{r["delta"]:+.1f} pts</span>
          </div>
          <div class="swing-cols">
            <div class="swing-col">
              <div class="swing-col-label">Baseline took</div>
              <div class="pill-list">{"".join(_position_pill(n, p) for n, p in only_base)}</div>
            </div>
            <div class="swing-col">
              <div class="swing-col-label">Model took instead</div>
              <div class="pill-list">{"".join(_position_pill(n, p) for n, p in only_model)}</div>
            </div>
          </div>
        </div>''')
    return f'<div class="swing-grid">{"".join(cards)}</div>'


def _calibration_section(calibration: dict | None) -> str:
    if not calibration:
        return ""
    gap = calibration["gap"]
    status = "good" if abs(gap) < 0.05 * max(abs(calibration["actual_total"]), 1) else "critical"
    direction = "overestimated" if gap > 0 else "underestimated"
    return f'''
    <section class="report-section">
      <h2>Confidence-weighted counterfactual estimate</h2>
      <p class="section-lede">At every genuine model decision (not the forced DST fill), instead of only
      scoring the single literal pick, blend the real point value of the top-2 candidate positions'
      best-available player &mdash; weighted by how close the model's own estimate was between them (a
      logistic function of the margin between them, scaled by their combined P25-P75 spread). A near-tied
      decision counts close to 50/50; a lopsided one counts close to 100/0. Not "calibration" in the strict
      sense (when the model says X% confidence, does it win X% of the time? &mdash; a real question, but a
      different one, worth building for the live tool separately) &mdash; this is an aggregate gap that can
      hide offsetting errors underneath it, since it uses realized counterfactual values rather than
      independent trials. Read it as a rough sanity check on the blend, not a precision instrument.</p>
      <div class="tile-row">
        {_stat_tile("blended estimate", f'{calibration["weighted_total"]:.0f} pts')}
        {_stat_tile("actual realized", f'{calibration["actual_total"]:.0f} pts')}
        {_stat_tile("gap", f'{gap:+.0f} pts', status,
                     sub=f'model {direction} on average')}
      </div>
    </section>'''


def _position_breakdown_section(
    breakdown: dict | None,
    title: str = "Where the edge comes from, by position",
    other_label: str = "Baseline",
    lede: str | None = None,
) -> str:
    if not breakdown:
        return ""
    rows = []
    for pos, row in sorted(breakdown.items(), key=lambda kv: -abs(kv[1]["delta"])):
        cl, cd = POSITION_COLOR.get(pos, ("#8993A4", "#7C8797"))
        status_cls = "positive" if row["delta"] > 0 else ("negative" if row["delta"] < 0 else "tie")
        rows.append(f'''
        <div class="pos-breakdown-row">
          <div class="pos-breakdown-label" style="--series:{cl};--series-dark:{cd}">{_esc(pos)}</div>
          <div class="pos-breakdown-value">{row["baseline_mean"]:.1f}</div>
          <div class="pos-breakdown-value">{row["model_mean"]:.1f}</div>
          <div class="pos-breakdown-delta {status_cls}">{row["delta"]:+.1f}</div>
          <div class="pos-breakdown-value">{row["win_rate"]:.0%}</div>
        </div>''')
    lede = lede or (
        f'''Mean <em>starter</em> contribution per replay (bench excluded, same discipline as the headline
      metric) for each position, under each strategy &mdash; not just the aggregate number, but where it
      actually concentrates. A FLEX starter is attributed to their real position, not a separate FLEX
      bucket.'''
    )
    return f'''
    <section class="report-section">
      <h2>{_esc(title)}</h2>
      <p class="section-lede">{lede}</p>
      <div class="pos-breakdown-table">
        <div class="pos-breakdown-row pos-breakdown-header">
          <div>Position</div><div>{_esc(other_label)}</div><div>Model</div><div>Delta</div><div>Win rate</div>
        </div>
        {"".join(rows)}
      </div>
    </section>'''


def _comparison_tiles(s: dict) -> str:
    return f'''
      <div class="tile-row">
        {_stat_tile("model win rate", f'{s["win_rate"]:.0%}', "good" if s["win_rate"] > 0.5 else "critical",
                     sub=f'95% CI {s["win_rate_ci"][0]:.0%}-{s["win_rate_ci"][1]:.0%}')}
        {_stat_tile("record (W-L-T)", f'{s["wins"]}-{s["losses"]}-{s["ties"]}',
                     sub=f'{s["n"]} replays, {s["n_clusters"]} cells, {s["n_seasons"]} seasons')}
        {_stat_tile("mean delta", f'{s["mean"]:+.1f} pts', "good" if s["mean"] > 0 else "critical",
                     sub=f'95% CI {s["mean_ci"][0]:+.1f} to {s["mean_ci"][1]:+.1f}')}
        {_stat_tile("median delta", f'{s["median"]:+.1f} pts', "good" if s["median"] > 0 else "critical",
                     sub=f'95% CI {s["median_ci"][0]:+.1f} to {s["median_ci"][1]:+.1f}')}
      </div>
      <div class="tile-row">
        {_stat_tile("worst loss", f'{s["min"]:+.1f} pts', "critical")}
        {_stat_tile("P10", f'{s["p10"]:+.1f} pts')}
        {_stat_tile("P90", f'{s["p90"]:+.1f} pts')}
        {_stat_tile("best win", f'{s["max"]:+.1f} pts', "good")}
      </div>'''


def render_report(data: dict) -> str:
    s = data["summary"]
    years_label = ", ".join(str(y) for y in data["years"])
    has_pure_adp = data.get("has_pure_adp", False)
    has_vor = data.get("has_vor", False)

    n_conditions = 2 + int(has_pure_adp) + int(has_vor)
    intro = (
        f'''{s["n"]} replayed draft states across {len(data["years"])} seasons &times;
      {len(data["slots"])} draft slots, each one replayed under {n_conditions} strategies sharing an
      identical opponent room &mdash; a realistic drafter taking the best-ADP player at a position of
      need (ADP+need), and this project's own Monte Carlo recommender, then every resulting roster
      scored on what actually happened that season. This is the real result, not a projection of one.'''
        + (
            ''' A simpler baseline (best ADP available, need ignored entirely) is compared too, so the
      report can separate "how much comes from beating pure chalk" from "how much comes from beating a
      drafter who at least manages need."'''
            if has_pure_adp else ""
        )
        + (
            ''' A fourth strategy reasons explicitly about value-over-replacement (real historical
      expected value per draft-rank bucket, minus a real replacement-level baseline for that position)
      &mdash; a genuinely stronger baseline than ADP+need, since it accounts for positional scarcity
      rather than just filling need at the best available ADP.'''
            if has_vor else ""
        )
    )

    pure_adp_section = ""
    if has_pure_adp:
        ps = data["summary_vs_pure_adp"]
        pure_adp_section = f'''
    <section class="report-section">
      <h2>Vs. pure ADP-chalk</h2>
      <p class="section-lede">The simplest possible drafter: always take the best-ADP player left, no
      positional need considered at all. Expected to be an easier bar to clear than the ADP+need baseline
      above &mdash; this is what separates "the model is smart" from "the model merely manages a roster
      better than a drafter who doesn't."</p>
      {_comparison_tiles(ps)}
    </section>

    <section class="report-section">
      <h2>Every (season, slot) cell &mdash; vs. pure ADP-chalk</h2>
      {_delta_chart(data["clusters_vs_pure_adp"], data["max_abs_cluster_vs_pure_adp"])}
    </section>'''

    vor_section = ""
    if has_vor:
        vs_ = data["summary_vs_vor"]
        vor_section = f'''
    <section class="report-section">
      <h2>Vs. value-over-replacement</h2>
      <p class="section-lede">A drafter reasoning explicitly about replacement value from real historical
      data &mdash; at each pick, take whichever available player at a position of need has the largest gap
      between their draft-rank bucket's real historical mean outcome and that position's real replacement
      level. Expected to be the hardest baseline to clear so far: it already prices in positional scarcity,
      which ADP+need does only implicitly (via the market) and pure ADP-chalk not at all.</p>
      {_comparison_tiles(vs_)}
    </section>

    <section class="report-section">
      <h2>Every (season, slot) cell &mdash; vs. VOR</h2>
      {_delta_chart(data["clusters_vs_vor"], data["max_abs_cluster_vs_vor"])}
    </section>
    {_position_breakdown_section(
        data.get("position_breakdown_vs_vor"),
        title="Where the model's edge over VOR concentrates, by position",
        other_label="VOR",
        lede='''VOR already reasons about positional scarcity &mdash; so wherever the model still beats it,
      that gap is evidence of something VOR&#39;s static per-pick calculation doesn&#39;t capture: rest-of-draft
      simulation, opponent behavior, uncertainty, or roster state. Same mean-starter-contribution discipline
      as the ADP+need breakdown above.''',
    )}'''

    body = f'''
    <header class="report-header">
      <p class="eyebrow">Backtest &middot; {years_label}</p>
      <h1>Does it actually beat drafting by ADP and need?</h1>
      <p class="report-intro">{intro}</p>
    </header>

    <section class="report-section">
      <h2>Vs. ADP + positional need</h2>
      {_comparison_tiles(s)}
      <p class="section-lede">Positive delta = the model's roster outscored the baseline's, on real points.
      All 95% CIs (win rate, mean, median) use a bootstrap that resamples whole <strong>seasons</strong>, not
      individual replays or even individual (year, slot) cells &mdash; different slots in the same season
      share that season's real player outcomes (one player's monster year can move several slots' results
      together) and aren't independent draws the way different seasons are. Clustering only at the cell level
      (as an earlier version of this report did) understates how uncertain these numbers actually are, since
      the real amount of independent evidence here is much closer to the season count than the replay count.</p>
    </section>
    {pure_adp_section}
    {vor_section}

    <section class="report-section">
      <h2>By season</h2>
      <p class="section-lede">Same replay design run separately per year (each using only outcome data from
      strictly before that year, so no season is graded on data the model was tuned from). The edge isn't
      perfectly uniform across seasons &mdash; worth watching as more years get added, not something to explain
      away from a handful of seasons. (Shown vs. the ADP+need baseline.)</p>
      {_per_year_tiles(data["per_year"])}
    </section>

    <section class="report-section">
      <h2>Every (season, slot) cell &mdash; vs. ADP+need</h2>
      <p class="section-lede">One row per season &times; draft slot, sorted best to worst for the model.
      The dot marks the mean across that cell's opponent-room seeds; the bar spans its min-to-max &mdash; a
      cell with only one seed so far just shows a point, not manufactured uncertainty. Green = model ahead on
      average, red = baseline ahead.</p>
      {_delta_chart(data["clusters"], data["max_abs_cluster"])}
    </section>

    <section class="report-section">
      <h2>What actually changed</h2>
      <p class="section-lede">The six biggest swings in either direction &mdash; the specific players each
      strategy took instead of the other. Only the picks that actually differ are shown; anything both
      strategies happened to draft is left out.</p>
      {_swing_cards(data["swings"])}
    </section>
    {_position_breakdown_section(data.get("position_breakdown"))}
    {_calibration_section(data.get("calibration"))}

    <section class="report-section">
      <h2>How to read this honestly</h2>
      <ul class="caveat-list">
        <li><strong>Still a small number of seasons.</strong> More opponent-room seeds per cell buys real
        statistical power without needing more historical seasons (which don't exist yet) &mdash; but every
        cell in a given season still shares that season's own real outcomes, and the CIs above now
        cluster-bootstrap at the season level specifically to reflect that (a bad or great season for the
        model's specific picks moves every slot within it together, not independently). With only a handful
        of seasons in scope, don't read a tight-looking interval as more certainty than a handful of seasons
        can actually provide.</li>
        <li><strong>DST scoring still doesn't exist.</strong> A separate bug from before &mdash; FFC's own
        ADP data for DST was being silently dropped by a position-code mismatch (fixed 2026-08-15, "DEF"/"PK"
        vs our "DST"/"K"), so DST is now correctly available and drafted at a realistic point. But no
        historical points source for DST exists anywhere in this codebase, so it still contributes 0 for both
        strategies &mdash; symmetric, doesn't bias the comparison, but both totals still understate true roster
        value slightly.</li>
        <li><strong>ADP source is FFC, not ESPN,</strong> and it isn't a frozen archive either &mdash; FFC's
        "historical" ADP for a past season can drift slightly over time as their own data keeps accumulating,
        confirmed empirically while rebuilding this report's caches. Numbers here are internally consistent
        with each other but not guaranteed bit-for-bit reproducible against a report generated on a different
        day.</li>
        <li><strong>num_sims per decision</strong> is lower than the live tool's default to keep this backtest's
        replay count tractable &mdash; a convergence check (does the recommendation actually stabilize with more
        sims) is still an open item.</li>
      </ul>
    </section>

    <footer class="report-footer">
      <p>Generated from {years_label} FFC ADP and nfl_data_py actuals, via
      <code>python -m fantasyprep.draft_sim.backtest</code>. Regenerate after any change to the outcome model,
      historical-year range, or backtest scope.</p>
    </footer>'''

    return f'''<!doctype html>
<title>ADP vs. Model Backtest</title>
<style>{_CSS}</style>
{body}'''


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    results = json.loads(args.in_path.read_text(encoding="utf-8"))
    data = gather_report_data(results)
    args.out.write_text(render_report(data), encoding="utf-8")
    print(f"Wrote {args.out}")


_CSS = '''
:root {
  color-scheme: light;
  --page: #F5F6F8;
  --surface: #FFFFFF;
  --ink: #161A22;
  --ink-2: #4B5566;
  --ink-3: #8993A4;
  --border: rgba(22,26,34,0.10);
  --grid: #E4E7EC;
  --accent: #B7791F;
  --good: #0ca30c;
  --good-bg: rgba(12,163,12,0.10);
  --critical: #d03b3b;
  --critical-bg: rgba(208,59,59,0.10);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0E1116;
    --surface: #161B22;
    --ink: #F2F4F7;
    --ink-2: #B7C0CC;
    --ink-3: #7C8797;
    --border: rgba(255,255,255,0.10);
    --grid: #2A3038;
    --accent: #E0A23D;
    --good: #0ca30c;
    --good-bg: rgba(12,163,12,0.16);
    --critical: #e66767;
    --critical-bg: rgba(230,103,103,0.14);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0E1116;
  --surface: #161B22;
  --ink: #F2F4F7;
  --ink-2: #B7C0CC;
  --ink-3: #7C8797;
  --border: rgba(255,255,255,0.10);
  --grid: #2A3038;
  --accent: #E0A23D;
  --good: #0ca30c;
  --good-bg: rgba(12,163,12,0.16);
  --critical: #e66767;
  --critical-bg: rgba(230,103,103,0.14);
}

* { box-sizing: border-box; }
body {
  background: var(--page);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0;
  padding: 2.5rem 1.25rem 5rem;
}
.report-header, .report-section, .report-footer {
  max-width: 980px;
  margin: 0 auto;
}
h1, h2, h3 {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  text-wrap: balance;
  font-weight: 600;
  color: var(--ink);
}
h1 { font-size: 2.1rem; margin: 0.2rem 0 0.9rem; }
h2 { font-size: 1.4rem; margin: 0 0 0.5rem; border-top: 1px solid var(--border); padding-top: 2rem; }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.75rem;
  color: var(--accent);
  font-weight: 600;
  margin: 0;
}
.report-intro, .section-lede {
  color: var(--ink-2);
  max-width: 68ch;
  line-height: 1.55;
}
.report-intro { font-size: 1.02rem; }
.section-lede { font-size: 0.92rem; }
code {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  background: var(--grid);
  padding: 0.1em 0.35em;
  border-radius: 4px;
  font-size: 0.88em;
}

.report-section { margin-bottom: 1rem; }

.tile-row {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin: 1rem 0 0.75rem;
}
.stat-tile {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.9rem 1.1rem;
  min-width: 150px;
  flex: 1 1 150px;
}
.stat-value {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  font-variant-numeric: tabular-nums;
  font-size: 1.5rem;
  font-weight: 600;
}
.stat-label {
  color: var(--ink-3);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-top: 0.2rem;
}
.stat-sub {
  color: var(--ink-3);
  font-size: 0.72rem;
  font-family: "SF Mono", Consolas, monospace;
  font-variant-numeric: tabular-nums;
  margin-top: 0.35rem;
}
.stat-tile.status-good .stat-value { color: var(--good); }
.stat-tile.status-critical .stat-value { color: var(--critical); }

.year-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin: 1rem 0; }
.year-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.9rem 1.1rem;
  text-align: center;
}
.year-card-title { font-family: Georgia, serif; font-size: 1.3rem; font-weight: 600; }
.year-card-record { color: var(--ink-3); font-size: 0.82rem; margin-top: 0.15rem; font-variant-numeric: tabular-nums; }
.year-card-mean { font-family: "SF Mono", Consolas, monospace; font-weight: 600; margin-top: 0.4rem; }
.stat-good { color: var(--good); }
.stat-critical { color: var(--critical); }
.stat-neutral { color: var(--ink-3); }

.delta-chart { display: flex; flex-direction: column; gap: 0.3rem; margin: 1rem 0; }
.delta-row { display: grid; grid-template-columns: 90px 1fr 64px; align-items: center; gap: 0.6rem; font-size: 0.8rem; }
.delta-label { color: var(--ink-2); font-family: "SF Mono", Consolas, monospace; font-size: 0.74rem; white-space: nowrap; }
.delta-track { position: relative; height: 12px; background: var(--grid); border-radius: 3px; overflow: visible; }
.delta-zero { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--ink-3); opacity: 0.5; }
.delta-range { position: absolute; top: 3px; bottom: 3px; border-radius: 2px; opacity: 0.35; min-width: 2px; }
.delta-range.positive { background: var(--good); }
.delta-range.negative { background: var(--critical); }
.delta-range.tie { background: var(--ink-3); }
.delta-mean {
  position: absolute; top: 50%; width: 8px; height: 8px; border-radius: 50%;
  transform: translate(-50%, -50%); border: 2px solid var(--surface);
}
.delta-mean.positive { background: var(--good); }
.delta-mean.negative { background: var(--critical); }
.delta-mean.tie { background: var(--ink-3); }
.delta-value { font-family: "SF Mono", Consolas, monospace; font-variant-numeric: tabular-nums; text-align: right; font-weight: 600; }
.delta-value.positive { color: var(--good); }
.delta-value.negative { color: var(--critical); }
.delta-value.tie { color: var(--ink-3); }

.swing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 0.75rem; margin: 1rem 0; }
.swing-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.9rem 1rem; }
.swing-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.6rem; }
.swing-title { font-weight: 600; font-size: 0.88rem; }
.swing-delta { font-family: "SF Mono", Consolas, monospace; font-weight: 600; font-size: 0.85rem; }
.swing-delta.positive { color: var(--good); }
.swing-delta.negative { color: var(--critical); }
.swing-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.swing-col-label { color: var(--ink-3); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; }
.pill-list { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.pill {
  display: inline-flex; align-items: center; gap: 0.3em;
  background: color-mix(in srgb, var(--series) 14%, var(--surface));
  color: var(--series);
  border-radius: 999px;
  padding: 0.15rem 0.55rem 0.15rem 0.55rem;
  font-size: 0.72rem;
  font-weight: 500;
  white-space: nowrap;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pill { color: var(--series-dark); background: color-mix(in srgb, var(--series-dark) 18%, var(--surface)); }
}
:root[data-theme="dark"] .pill { color: var(--series-dark); background: color-mix(in srgb, var(--series-dark) 18%, var(--surface)); }
.pill-pos { opacity: 0.7; font-size: 0.9em; }

.caveat-list { color: var(--ink-2); font-size: 0.9rem; line-height: 1.6; max-width: 72ch; padding-left: 1.2rem; }
.caveat-list li { margin-bottom: 0.5rem; }
.caveat-list strong { color: var(--ink); }

.pos-breakdown-table {
  margin: 1rem 0; border: 1px solid var(--border); border-radius: 10px;
  overflow-x: auto;
}
.pos-breakdown-row {
  display: grid; grid-template-columns: 1.2fr 1fr 1fr 1fr 1fr; min-width: 420px;
  padding: 0.6rem 0.9rem; font-size: 0.85rem; align-items: center;
  border-bottom: 1px solid var(--border);
}
.pos-breakdown-row:last-child { border-bottom: none; }
.pos-breakdown-row:not(.pos-breakdown-header):nth-child(even) { background: var(--page); }
.pos-breakdown-header {
  background: var(--page); color: var(--ink-3); font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600;
}
.pos-breakdown-label { font-weight: 600; color: var(--series); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pos-breakdown-label { color: var(--series-dark); }
}
:root[data-theme="dark"] .pos-breakdown-label { color: var(--series-dark); }
.pos-breakdown-value {
  font-family: "SF Mono", Consolas, monospace; font-variant-numeric: tabular-nums; color: var(--ink-2);
}
.pos-breakdown-delta {
  font-family: "SF Mono", Consolas, monospace; font-variant-numeric: tabular-nums; font-weight: 600;
}
.pos-breakdown-delta.positive { color: var(--good); }
.pos-breakdown-delta.negative { color: var(--critical); }
.pos-breakdown-delta.tie { color: var(--ink-3); }

.report-footer {
  margin-top: 2.5rem;
  padding-top: 1.2rem;
  border-top: 1px solid var(--border);
  color: var(--ink-3);
  font-size: 0.8rem;
}

@media (max-width: 700px) {
  .swing-cols { grid-template-columns: 1fr; }
  .delta-row { grid-template-columns: 70px 1fr 56px; font-size: 0.72rem; }
}
'''

if __name__ == "__main__":
    main()
