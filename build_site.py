#!/usr/bin/env python3
"""Genere le dashboard statique (docs/index.html) a partir des donnees scrapees.

Sources affichees :
  - data/polls.csv            : sondages (intentions de vote), via scrape_polls.py
  - data/markets.csv          : marche predictif Polymarket (proba de victoire),
                                via scrape_markets.py
  - candidates.csv            : registre des candidatures (statuts sources)

Page autonome (HTML/CSS/JS inline, sans dependance externe), theme clair/sombre.

Usage :
    python build_site.py                    # ecrit docs/index.html
"""
from __future__ import annotations

import argparse
import json
import html
from pathlib import Path

import pandas as pd

# Palette categorielle validee (dataviz) : [light, dark] par slot.
PALETTE = [
    ("#2a78d6", "#3987e5"),  # blue
    ("#1baf7a", "#199e70"),  # aqua
    ("#eda100", "#c98500"),  # yellow
    ("#008300", "#008300"),  # green
    ("#4a3aa7", "#9085e9"),  # violet
    ("#e34948", "#e66767"),  # red
    ("#e87ba4", "#d55181"),  # magenta
    ("#eb6834", "#d95926"),  # orange
]
MAX_SERIES = len(PALETTE)

STATUS_LABELS = {
    "declared": "Candidature déclarée",
    "likely": "Candidature probable",
    "undecided": "Position non tranchée",
    "withdrawn": "A renoncé / rallié·e",
    "ineligible": "Inéligible",
    # anciens statuts, encore acceptes
    "candidate": "Candidature déclarée",
    "undeclared": "Position non tranchée",
    "not_candidate": "A renoncé / inéligible",
}
STATUS_ORDER = ["declared", "likely", "undecided", "withdrawn", "ineligible",
                "candidate", "undeclared", "not_candidate"]
STATUS_CLASS = {
    "declared": "ok", "candidate": "ok",
    "likely": "soon",
    "undecided": "wait", "undeclared": "wait",
    "withdrawn": "out", "ineligible": "out", "not_candidate": "out",
    "notcandidate": "out",
}
BADGE_TXT = {
    "declared": "déclaré·e", "candidate": "déclaré·e",
    "likely": "probable",
    "undecided": "indécis·e", "undeclared": "indécis·e",
    "withdrawn": "a renoncé", "ineligible": "inéligible",
    "notcandidate": "non-candidat", "not_candidate": "non-candidat",
    "unknown": "?",
}


def surname(name: str) -> str:
    """Nom court pour les labels de courbe (dernier mot significatif)."""
    n = name.split("(")[0].strip()
    parts = n.split()
    if len(parts) >= 2 and parts[-2].lower() in {"le", "de", "van", "von"}:
        return " ".join(parts[-2:])
    return parts[-1] if parts else name


def load_candidates(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["display_name", "party", "status"])
    return pd.read_csv(path, dtype=str).fillna("")


def build_poll_series(df: pd.DataFrame) -> list[dict]:
    """Series temporelles des intentions de vote (1er tour, top candidats)."""
    first = df[df["section"].str.contains("First round", case=False, na=False)].copy()
    if first.empty:
        first = df.copy()
    first = first.dropna(subset=["fieldwork_end", "percentage"])
    if first.empty:
        return []

    agg = (
        first.groupby(["candidate", "fieldwork_end"], as_index=False)["percentage"]
        .mean()
        .sort_values("fieldwork_end")
    )
    latest = (
        agg.sort_values("fieldwork_end")
        .groupby("candidate", as_index=False)
        .last()
        .sort_values("percentage", ascending=False)
    )
    # Ne trace que les candidats encore testes recemment (sinon la courbe
    # s'arrete au milieu du graphe : Le Maire, Borne... sortis des sondages).
    max_date = str(agg["fieldwork_end"].max())
    cutoff = f"{int(max_date[:4]) - 1}{max_date[4:]}"  # ~12 mois avant la fin
    latest = latest[latest["fieldwork_end"] >= cutoff]
    top = sorted(latest.head(MAX_SERIES)["candidate"].tolist())

    series = []
    for cand in top:
        pts = agg[agg["candidate"] == cand]
        points = [
            {"x": d, "y": round(float(y), 1)}
            for d, y in zip(pts["fieldwork_end"], pts["percentage"])
        ]
        if points:
            series.append({"name": cand, "short": surname(cand), "points": points})
    return series


def build_market_series(df: pd.DataFrame, min_last_prob: float = 2.0) -> list[dict]:
    """Series temporelles des probabilites Polymarket (top candidats)."""
    if df.empty:
        return []
    df = df.dropna(subset=["date", "prob_pct"]).copy()
    latest = (
        df.sort_values("date")
        .groupby("candidate", as_index=False)
        .last()
        .sort_values("prob_pct", ascending=False)
    )
    keep = latest[latest["prob_pct"] >= min_last_prob].head(MAX_SERIES)
    top = sorted(keep["candidate"].tolist())

    series = []
    for cand in top:
        pts = df[df["candidate"] == cand].sort_values("date")
        points = [
            {"x": d, "y": round(float(p), 1)}
            for d, p in zip(pts["date"], pts["prob_pct"])
        ]
        if points:
            series.append({"name": cand, "short": surname(cand), "points": points})
    return series


def build_latest_table(df: pd.DataFrame, cand_df: pd.DataFrame,
                       market_snap: pd.DataFrame) -> list[dict]:
    """Derniere valeur sondage + proba marche par candidat."""
    sub = df.dropna(subset=["percentage"]).copy()
    if sub.empty:
        return []
    status_by_name = {
        r["display_name"]: r.get("status", "") for _, r in cand_df.iterrows()
    }
    party_by_name = {
        r["display_name"]: r.get("party", "") for _, r in cand_df.iterrows()
    }
    market_by_name = {}
    if not market_snap.empty:
        for _, r in market_snap.iterrows():
            market_by_name[str(r["candidate"])] = float(r["prob_pct"])

    latest = (
        sub.sort_values("fieldwork_end")
        .groupby("candidate", as_index=False)
        .last()
        .sort_values("percentage", ascending=False)
    )
    rows = []
    for _, r in latest.iterrows():
        name = r["candidate"]
        # rapprochement souple avec le marche (nom exact ou nom de famille)
        mkt = market_by_name.get(name)
        if mkt is None:
            sn = surname(name).lower()
            for mname, mprob in market_by_name.items():
                if surname(mname).lower() == sn:
                    mkt = mprob
                    break
        rows.append({
            "candidate": name,
            "party": party_by_name.get(name, ""),
            "status": r.get("candidate_status", "") or status_by_name.get(name, ""),
            "percentage": round(float(r["percentage"]), 1),
            "market_prob": mkt,
            "pollster": r.get("pollster", ""),
            "date": r.get("fieldwork_end", "") or r.get("fieldwork_raw", ""),
        })
    return rows


def build_status_groups(cand_df: pd.DataFrame) -> dict:
    groups = {s: [] for s in STATUS_ORDER}
    for _, r in cand_df.iterrows():
        st = (r.get("status", "") or "undecided").strip()
        if st not in groups:
            groups.setdefault(st, [])
        groups[st].append({
            "name": r.get("display_name", ""),
            "party": r.get("party", ""),
            "declared_on": r.get("declared_on", ""),
            "source": r.get("source", ""),
            "notes": r.get("notes", ""),
        })
    return groups


def render_html(poll_series, market_series, latest_rows, status_groups,
                meta) -> str:
    data_json = json.dumps(
        {"polls": poll_series, "markets": market_series, "palette": PALETTE},
        ensure_ascii=False,
    )

    # --- Cartes de suivi des candidatures ---
    status_html = []
    for st in STATUS_ORDER:
        people = status_groups.get(st, [])
        if not people:
            continue
        chips = []
        for p in people:
            party = f" · {html.escape(p['party'])}" if p["party"] else ""
            declared = ""
            if p["declared_on"]:
                declared = f"<span class='date'>{html.escape(p['declared_on'])}</span>"
            src = str(p.get("source", "") or "")
            name_html = html.escape(p["name"])
            if src.startswith("http"):
                name_html = (f"<a href='{html.escape(src)}' target='_blank' "
                             f"rel='noopener'>{name_html}</a>")
            note = str(p.get("notes", "") or "")
            note_html = (f"<span class='note-inline'>{html.escape(note)}</span>"
                         if note else "")
            chips.append(
                f"<li><span class='dot'></span>"
                f"<span class='nm'>{name_html}</span>"
                f"<span class='pt'>{party}</span>{declared}{note_html}</li>"
            )
        status_html.append(
            f"<div class='statcard {STATUS_CLASS.get(st, '')}'>"
            f"<h3>{html.escape(STATUS_LABELS.get(st, st))} "
            f"<span class='count'>{len(people)}</span></h3>"
            f"<ul>{''.join(chips)}</ul></div>"
        )

    # --- Tableau des dernieres valeurs ---
    trs = []
    for r in latest_rows:
        st = str(r["status"] or "")
        badge_cls = STATUS_CLASS.get(st, "unk")
        badge_txt = BADGE_TXT.get(st, st or "?")
        mkt = (f"{r['market_prob']:.1f}%" if r.get("market_prob") is not None
               else "—")
        trs.append(
            f"<tr><td class='nm'>{html.escape(r['candidate'])}</td>"
            f"<td class='muted'>{html.escape(r['party'])}</td>"
            f"<td class='num'>{r['percentage']:.1f}%</td>"
            f"<td class='num'>{mkt}</td>"
            f"<td><span class='badge {badge_cls}'>{html.escape(badge_txt)}</span></td>"
            f"<td class='muted'>{html.escape(str(r['pollster']))}</td>"
            f"<td class='muted num'>{html.escape(str(r['date']))}</td></tr>"
        )
    table_html = "".join(trs) or "<tr><td colspan='7' class='muted'>Aucune donnée.</td></tr>"

    return TEMPLATE.format(
        data_json=data_json,
        status_cards="".join(status_html),
        table_rows=table_html,
        verified=html.escape(meta.get("verified", "—")),
        updated=html.escape(meta.get("updated", "")),
        n_polls=meta.get("n_polls", 0),
        n_pollsters=meta.get("n_pollsters", 0),
        market_leader=html.escape(meta.get("market_leader", "—")),
        market_volume=html.escape(meta.get("market_volume", "—")),
    )


TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr" data-palette="#2a78d6,#1baf7a,#eda100,#008300,#4a3aa7,#e34948,#e87ba4,#eb6834">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sondages présidentielle 2027</title>
<style>
  :root {{
    --surface-1:#fcfcfb; --page:#f9f9f7; --text-1:#0b0b0b; --text-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
    --ok:#0ca30c; --wait:#eda100; --out:#d03b3b; --soon:#2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface-1:#1a1a19; --page:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7;
      --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
      --ok:#0ca30c; --wait:#c98500; --out:#e66767; --soon:#3987e5;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--page); color:var(--text-1);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.5; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:32px 20px 64px; }}
  header h1 {{ font-size:1.7rem; margin:0 0 4px; }}
  header p {{ color:var(--text-2); margin:0; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:20px; margin:20px 0 8px; }}
  .meta div {{ background:var(--surface-1); border:1px solid var(--border);
    border-radius:10px; padding:12px 16px; min-width:120px; }}
  .meta .big {{ font-size:1.5rem; font-weight:600; }}
  .meta .lbl {{ color:var(--muted); font-size:.82rem; }}
  h2 {{ font-size:1.15rem; margin:36px 0 12px; }}
  .card {{ background:var(--surface-1); border:1px solid var(--border);
    border-radius:12px; padding:16px; overflow-x:auto; }}
  .note {{ background:var(--surface-1); border:1px solid var(--border);
    border-left:3px solid var(--wait); border-radius:8px; padding:12px 16px;
    color:var(--text-2); font-size:.9rem; margin:12px 0; }}
  /* charts */
  .chart {{ width:100%; height:auto; display:block; }}
  .grid line {{ stroke:var(--grid); stroke-width:1; }}
  .axis line {{ stroke:var(--axis); stroke-width:1; }}
  .axis text, .tick {{ fill:var(--muted); font-size:12px; }}
  .serie {{ fill:none; stroke-width:2; }}
  .endlbl {{ font-size:12px; font-weight:600; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:14px; }}
  .legend span {{ display:inline-flex; align-items:center; gap:6px;
    font-size:.85rem; color:var(--text-2); }}
  .legend i {{ width:12px; height:3px; border-radius:2px; display:inline-block; }}
  .cross {{ stroke:var(--axis); stroke-width:1; stroke-dasharray:3 3; }}
  #tt {{ position:fixed; pointer-events:none; background:var(--surface-1);
    border:1px solid var(--border); border-radius:8px; padding:8px 10px;
    font-size:.82rem; box-shadow:0 4px 16px rgba(0,0,0,.15); opacity:0;
    transition:opacity .08s; z-index:10; max-width:260px; }}
  #tt .d {{ color:var(--muted); margin-bottom:4px; }}
  #tt div.row {{ display:flex; justify-content:space-between; gap:12px; }}
  #tt b {{ font-variant-numeric:tabular-nums; }}
  /* status cards */
  .statgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
    gap:14px; }}
  .statcard {{ background:var(--surface-1); border:1px solid var(--border);
    border-radius:12px; padding:14px 16px; border-top:3px solid var(--muted); }}
  .statcard.ok {{ border-top-color:var(--ok); }}
  .statcard.out {{ border-top-color:var(--out); }}
  .statcard.wait {{ border-top-color:var(--wait); }}
  .statcard.soon {{ border-top-color:var(--soon); }}
  .statcard h3 {{ font-size:.95rem; margin:0 0 10px; display:flex;
    justify-content:space-between; align-items:center; }}
  .statcard .count {{ background:var(--page); border:1px solid var(--border);
    border-radius:20px; padding:1px 9px; font-size:.8rem; color:var(--text-2); }}
  .statcard ul {{ list-style:none; margin:0; padding:0; }}
  .statcard li {{ display:flex; align-items:baseline; gap:6px; padding:3px 0;
    font-size:.9rem; flex-wrap:wrap; }}
  .statcard .dot {{ width:7px; height:7px; border-radius:50%; flex:0 0 auto;
    background:var(--muted); }}
  .statcard.ok .dot {{ background:var(--ok); }}
  .statcard.out .dot {{ background:var(--out); }}
  .statcard.wait .dot {{ background:var(--wait); }}
  .statcard.soon .dot {{ background:var(--soon); }}
  .statcard .nm {{ font-weight:600; }}
  .statcard .nm a {{ text-decoration-color:var(--muted); }}
  .statcard .pt, .statcard .date {{ color:var(--muted); font-size:.82rem; }}
  .statcard .date {{ margin-left:auto; }}
  .statcard .note-inline {{ flex-basis:100%; color:var(--muted);
    font-size:.78rem; padding-left:13px; }}
  /* table */
  table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }}
  th {{ color:var(--muted); font-weight:600; font-size:.8rem; text-transform:uppercase;
    letter-spacing:.03em; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.nm {{ font-weight:600; }}
  td.muted {{ color:var(--text-2); }}
  .badge {{ font-size:.75rem; padding:2px 8px; border-radius:20px;
    border:1px solid var(--border); }}
  .badge.ok {{ color:var(--ok); }}
  .badge.out {{ color:var(--out); }}
  .badge.wait {{ color:var(--wait); }}
  .badge.soon {{ color:var(--soon); }}
  .badge.unk {{ color:var(--muted); }}
  footer {{ margin-top:40px; color:var(--muted); font-size:.82rem; }}
  a {{ color:inherit; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Sondages — élection présidentielle 2027</h1>
    <p>Intentions de vote (sondages) et probabilités de victoire (marché
       prédictif) · sources&nbsp;:
       <a href="https://en.wikipedia.org/wiki/Opinion_polling_for_the_2027_French_presidential_election">Wikipédia</a> ·
       <a href="https://polymarket.com/event/next-french-presidential-election">Polymarket</a></p>
  </header>

  <div class="meta">
    <div><div class="big">{n_polls}</div><div class="lbl">sondages retenus</div></div>
    <div><div class="big">{n_pollsters}</div><div class="lbl">instituts</div></div>
    <div><div class="big">{market_leader}</div><div class="lbl">favori·te du marché</div></div>
    <div><div class="big">{market_volume}</div><div class="lbl">volume Polymarket</div></div>
    <div><div class="big">{updated}</div><div class="lbl">dernière mise à jour</div></div>
  </div>

  <h2>Probabilité de victoire — marché prédictif Polymarket</h2>
  <div class="card">
    <svg id="mchart" class="chart" viewBox="0 0 960 440" preserveAspectRatio="xMidYMid meet"
         role="img" aria-label="Évolution des probabilités de victoire sur Polymarket"></svg>
    <div class="legend" id="mlegend"></div>
  </div>
  <p class="note">Probabilité implicite (prix du marché) que chaque personnalité
    remporte l'élection — signal continu, réagit à l'actualité en temps réel.
    Ce n'est <b>pas</b> une intention de vote.</p>

  <h2>Intentions de vote — 1<sup>er</sup> tour (sondages)</h2>
  <div class="card">
    <svg id="pchart" class="chart" viewBox="0 0 960 440" preserveAspectRatio="xMidYMid meet"
         role="img" aria-label="Courbes d'évolution des intentions de vote"></svg>
    <div class="legend" id="plegend"></div>
  </div>
  <p class="note">Hypothèses de 1<sup>er</sup> tour retenues par le filtre du
    registre des candidatures ; plusieurs instituts un même jour sont moyennés.
    Le tableau en bas de page liste tous les candidats testés.</p>

  <h2>Suivi des candidatures</h2>
  <div class="statgrid">{status_cards}</div>
  <p class="note">Statuts vérifiés sur sources de presse (cliquer un nom ouvre sa
    source) — dossier détaillé avec citations :
    <a href="https://github.com/sbbb-git/presi/blob/claude/election-poll-scraper-33nqxx/declarations.md">declarations.md</a>
    · dernière vérification&nbsp;: {verified}</p>

  <h2>Dernière valeur par candidat</h2>
  <div class="card">
    <table>
      <thead><tr><th>Candidat</th><th>Parti</th><th class="num">Sondage</th>
        <th class="num">Marché</th><th>Statut</th><th>Institut</th>
        <th class="num">Terrain</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>

  <footer>
    Généré automatiquement depuis <code>data/polls.csv</code> (sondages, via
    Wikipédia) et <code>data/markets.csv</code> (Polymarket). Le filtrage des
    hypothèses est piloté par <code>candidates.csv</code>. Intentions de vote et
    probabilités de marché sont des estimations, sujettes aux marges d'erreur et
    à la volatilité. Cette page n'est affiliée à aucun institut ni plateforme.
  </footer>
</div>

<div id="tt"></div>
<script>
const DATA = {data_json};
(function() {{
  const NS = 'http://www.w3.org/2000/svg';
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const color = i => DATA.palette[i % DATA.palette.length][dark ? 1 : 0];
  const tt = document.getElementById('tt');
  const mk = (n, a) => {{ const e = document.createElementNS(NS, n);
    for (const k in a) e.setAttribute(k, a[k]); return e; }};

  function drawChart(svgId, legendId, series, unit) {{
    const svg = document.getElementById(svgId);
    const legend = document.getElementById(legendId);
    const W = 960, H = 440, M = {{t:20, r:110, b:36, l:44}};
    if (!series.length) {{
      svg.innerHTML = '<text x="20" y="40" fill="#898781">Pas de données.</text>';
      return;
    }}
    const allX = [...new Set(series.flatMap(s => s.points.map(p => p.x)))].sort();
    const t0 = new Date(allX[0]).getTime(), t1 = new Date(allX[allX.length-1]).getTime();
    let ymax = 0;
    series.forEach(s => s.points.forEach(p => {{ if (p.y > ymax) ymax = p.y; }}));
    ymax = Math.ceil(ymax / 5) * 5 || 5;
    const step = ymax > 25 ? 10 : 5;
    const px = t => M.l + (t - t0) / (t1 - t0 || 1) * (W - M.l - M.r);
    const py = v => H - M.b - v / ymax * (H - M.t - M.b);

    const g = mk('g', {{class:'grid'}});
    for (let v = 0; v <= ymax; v += step) {{
      g.appendChild(mk('line', {{x1:M.l, x2:W-M.r, y1:py(v), y2:py(v)}}));
      const tx = mk('text', {{x:M.l-8, y:py(v)+4, 'text-anchor':'end', class:'tick'}});
      tx.textContent = v + '%'; g.appendChild(tx);
    }}
    svg.appendChild(g);

    const ax = mk('g', {{class:'axis'}});
    ax.appendChild(mk('line', {{x1:M.l, x2:W-M.r, y1:H-M.b, y2:H-M.b}}));
    // ticks : annees + debuts de trimestre si l'intervalle est court
    const months = (t1 - t0) / 2.63e9;
    const tickDates = [];
    const d0 = new Date(t0), d1 = new Date(t1);
    for (let y = d0.getUTCFullYear(); y <= d1.getUTCFullYear(); y++) {{
      for (let m = 0; m < 12; m += (months > 30 ? 12 : 3)) {{
        const d = new Date(Date.UTC(y, m, 1));
        if (d.getTime() >= t0 && d.getTime() <= t1) tickDates.push(d);
      }}
    }}
    tickDates.forEach(d => {{
      const lbl = d.getUTCMonth() === 0 ? String(d.getUTCFullYear())
        : (d.toLocaleDateString('fr-FR', {{month:'short'}}) + ' ' + String(d.getUTCFullYear()).slice(2));
      const tx = mk('text', {{x:px(d.getTime()), y:H-M.b+18, 'text-anchor':'middle', class:'tick'}});
      tx.textContent = lbl; ax.appendChild(tx);
    }});
    svg.appendChild(ax);

    // occupation verticale des labels de fin pour eviter les collisions
    const usedY = [];
    series.forEach((s, i) => {{
      const pts = s.points.map(p => [px(new Date(p.x).getTime()), py(p.y)]);
      const d = pts.map((p, j) => (j ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
      svg.appendChild(mk('path', {{d:d, class:'serie', stroke:color(i)}}));
      const last = pts[pts.length - 1];
      let ly = last[1] + 4;
      while (usedY.some(u => Math.abs(u - ly) < 13)) ly += 13;
      usedY.push(ly);
      const lbl = mk('text', {{x:last[0]+8, y:ly, class:'endlbl', fill:color(i)}});
      lbl.textContent = s.short; svg.appendChild(lbl);
    }});

    legend.innerHTML = '';
    series.forEach((s, i) => {{
      const el = document.createElement('span');
      el.innerHTML = '<i style="background:' + color(i) + '"></i>' + s.name;
      legend.appendChild(el);
    }});

    const cross = mk('line', {{class:'cross', y1:M.t, y2:H-M.b, x1:0, x2:0}});
    cross.style.opacity = 0; svg.appendChild(cross);
    const dots = [];
    const overlay = mk('rect', {{x:M.l, y:M.t, width:W-M.l-M.r, height:H-M.t-M.b,
      fill:'transparent'}});
    svg.appendChild(overlay);
    overlay.addEventListener('mousemove', evt => {{
      const pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      const mx = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
      let best = allX[0], bd = Infinity;
      allX.forEach(ds => {{ const dx = Math.abs(px(new Date(ds).getTime()) - mx);
        if (dx < bd) {{ bd = dx; best = ds; }} }});
      const X = px(new Date(best).getTime());
      cross.setAttribute('x1', X); cross.setAttribute('x2', X); cross.style.opacity = 1;
      dots.forEach(d => d.remove()); dots.length = 0;
      let rows = '';
      series.forEach((s, i) => {{
        const p = s.points.find(p => p.x === best);
        if (!p) return;
        const dot = mk('circle', {{cx:X, cy:py(p.y), r:4, fill:color(i),
          stroke:dark ? '#1a1a19' : '#fcfcfb', 'stroke-width':2}});
        svg.appendChild(dot); dots.push(dot);
        rows += '<div class="row"><span>' + s.short + '</span><b>' + p.y.toFixed(1) + unit + '</b></div>';
      }});
      tt.innerHTML = '<div class="d">' + best + '</div>' + rows;
      tt.style.opacity = 1;
      tt.style.left = Math.min(evt.clientX + 14, innerWidth - 220) + 'px';
      tt.style.top = (evt.clientY + 14) + 'px';
    }});
    overlay.addEventListener('mouseleave', () => {{
      cross.style.opacity = 0; tt.style.opacity = 0;
      dots.forEach(d => d.remove()); dots.length = 0;
    }});
  }}

  drawChart('mchart', 'mlegend', DATA.markets, '%');
  drawChart('pchart', 'plegend', DATA.polls, '%');
}})();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--polls", default="data/polls.csv")
    ap.add_argument("--markets", default="data/markets.csv")
    ap.add_argument("--markets-snapshot", default="data/markets_snapshot.csv")
    ap.add_argument("--candidates", default="candidates.csv")
    ap.add_argument("--out", default="docs/index.html")
    args = ap.parse_args()

    polls_path = Path(args.polls)
    if not polls_path.exists():
        print(f"Fichier introuvable : {polls_path}. Lance d'abord scrape_polls.py.")
        return 1

    df = pd.read_csv(polls_path, dtype={"percentage": float})
    cand_df = load_candidates(Path(args.candidates))

    markets_path = Path(args.markets)
    mdf = (pd.read_csv(markets_path) if markets_path.exists()
           else pd.DataFrame(columns=["date", "candidate", "prob_pct"]))
    snap_path = Path(args.markets_snapshot)
    snap = (pd.read_csv(snap_path) if snap_path.exists()
            else pd.DataFrame(columns=["candidate", "prob_pct", "volume_usd"]))

    poll_series = build_poll_series(df)
    market_series = build_market_series(mdf)
    latest_rows = build_latest_table(df, cand_df, snap)
    status_groups = build_status_groups(cand_df)

    updated = ""
    if "scraped_at" in df.columns and not df["scraped_at"].dropna().empty:
        updated = str(df["scraped_at"].dropna().max())
    if not mdf.empty:
        updated = max(updated, str(mdf["date"].max()))

    verified = ""
    if "checked_on" in cand_df.columns:
        vals = [v for v in cand_df["checked_on"].astype(str) if v and v != "nan"]
        verified = max(vals) if vals else ""

    market_leader = "—"
    market_volume = "—"
    if not snap.empty:
        lead = snap.sort_values("prob_pct", ascending=False).iloc[0]
        market_leader = f"{surname(str(lead['candidate']))} {lead['prob_pct']:.0f}%"
        if "volume_usd" in snap.columns:
            market_volume = f"{snap['volume_usd'].sum()/1e6:.0f} M$"

    meta = {
        "updated": updated,
        "verified": verified or "—",
        "n_polls": int(df.drop_duplicates(
            ["hypothesis_id", "pollster", "fieldwork_raw"]).shape[0])
            if "hypothesis_id" in df.columns else int(df.shape[0]),
        "n_pollsters": int(df["pollster"].nunique()),
        "market_leader": market_leader,
        "market_volume": market_volume,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(poll_series, market_series, latest_rows,
                               status_groups, meta), encoding="utf-8")
    print(f"Dashboard genere : {out} ({len(market_series)} courbes marche, "
          f"{len(poll_series)} courbes sondages, "
          f"{len(latest_rows)} candidats au tableau).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
