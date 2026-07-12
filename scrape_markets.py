#!/usr/bin/env python3
"""Collecte des probabilites implicites de plusieurs marches predictifs pour
l'election presidentielle francaise de 2027, et calcul d'un consensus.

Sources croisees (API publiques, sans authentification) :
  - Polymarket  (argent reel, crypto)      poids 1.0
  - Kalshi      (argent reel, regule CFTC)  poids 1.0
  - Manifold    (play money, communautaire) poids 0.5

Croiser plusieurs marches donne un signal plus robuste : une plateforme peut
etre illiquide ou en retard sur l'actualite, le consensus lisse ces biais.
Le signal mesure une probabilite de VICTOIRE (pas des intentions de vote).

Sorties :
  - data/markets.csv            : historique quotidien par (source, candidat)
  - data/markets_consensus.csv  : consensus quotidien (moyenne ponderee)
  - data/markets_snapshot.csv   : instantane du jour par (source, candidat)
                                  + lignes source="consensus"

Usage :
    python scrape_markets.py
    python scrape_markets.py --min-prob 1.0   # seuil d'inclusion dans l'historique
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

USER_AGENT = (
    "presi-2027-poll-scraper/1.0 "
    "(https://github.com/sbbb-git/presi; open election data project)"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Poids de chaque source dans le consensus (argent reel > play money).
SOURCE_WEIGHTS = {"polymarket": 1.0, "kalshi": 1.0, "manifold": 0.5}

# Debut de l'historique retenu : les marches a argent reel (Polymarket, Kalshi)
# ont ouvert fin oct./nov. 2025. Avant, seul un vieux marche Manifold peu liquide
# existe (depuis 2023) — on l'ecarte pour ne pas polluer le consensus.
HISTORY_START = "2025-10-15"

# --- Configuration des sources ----------------------------------------------
POLYMARKET_SLUG = "next-french-presidential-election"
KALSHI_EVENT = "KXFRENCHPRES-27"
KALSHI_SERIES = "KXFRENCHPRES"
# Marches Manifold suivis (les deux plus fournis en parieurs).
MANIFOLD_SLUGS = [
    "who-will-be-the-next-president-of-f-11b98432cfee",
    "who-will-be-the-next-president-of-f",
]
# Reponses generiques a ignorer (pas des candidats).
GENERIC = {"other", "others", "someone else", "no one", "nobody", "field"}


def canonical(name: str) -> str:
    """Cle de rapprochement d'un candidat entre plateformes (sans accents)."""
    n = unicodedata.normalize("NFKD", str(name))
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def get(url: str, params: dict | None = None) -> object:
    resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


# --- Polymarket -------------------------------------------------------------
def fetch_polymarket(track: set[str] | None) -> tuple[list, list]:
    events = get("https://gamma-api.polymarket.com/events",
                 {"slug": POLYMARKET_SLUG})
    if not events:
        return [], []
    event = events[0]
    snap, tokens = [], {}
    for m in event.get("markets", []):
        name = (m.get("groupItemTitle") or m.get("question") or "").strip()
        if not name or canonical(name) in GENERIC:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            tok = json.loads(m.get("clobTokenIds") or "[]")
        except (TypeError, ValueError):
            continue
        if not prices or not tok:
            continue
        try:
            prob = float(prices[0]) * 100
        except (TypeError, ValueError):
            continue
        vol = float(m.get("volumeNum") or m.get("volume") or 0)
        snap.append({"source": "polymarket", "candidate": name,
                     "prob_pct": round(prob, 2), "volume_usd": round(vol, 2)})
        if not bool(m.get("closed")):
            tokens[name] = str(tok[0])

    hist = []
    for name, token in tokens.items():
        if track is not None and canonical(name) not in track:
            continue
        try:
            data = get("https://clob.polymarket.com/prices-history",
                       {"market": token, "interval": "max", "fidelity": 1440})
        except requests.RequestException:
            continue
        by_day = {}
        for p in data.get("history", []):
            day = datetime.fromtimestamp(p["t"], tz=timezone.utc).strftime("%Y-%m-%d")
            by_day[day] = float(p["p"]) * 100
        for day, prob in by_day.items():
            hist.append({"source": "polymarket", "candidate": name,
                         "date": day, "prob_pct": round(prob, 2)})
        time.sleep(0.2)
    return snap, hist


# --- Kalshi -----------------------------------------------------------------
def fetch_kalshi(track: set[str] | None) -> tuple[list, list]:
    data = get("https://api.elections.kalshi.com/trade-api/v2/markets",
               {"event_ticker": KALSHI_EVENT, "limit": 200})
    markets = data.get("markets", [])
    snap, tickers = [], {}
    for m in markets:
        name = (m.get("yes_sub_title") or m.get("title") or "").strip()
        if not name or canonical(name) in GENERIC:
            continue
        price = None
        for k in ("last_price_dollars", "previous_price_dollars",
                  "yes_bid_dollars", "yes_ask_dollars"):
            v = m.get(k)
            if v is not None:
                try:
                    price = float(v)
                    break
                except (TypeError, ValueError):
                    pass
        if price is None:
            continue
        vol = m.get("volume") or m.get("notional_value_dollars") or 0
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = 0.0
        snap.append({"source": "kalshi", "candidate": name,
                     "prob_pct": round(price * 100, 2), "volume_usd": round(vol, 2)})
        if m.get("status") not in ("finalized", "settled"):
            tickers[name] = m.get("ticker")

    hist = []
    now = int(datetime.now(tz=timezone.utc).timestamp())
    start = now - 260 * 86400
    for name, ticker in tickers.items():
        if track is not None and canonical(name) not in track:
            continue
        try:
            cd = get(f"https://api.elections.kalshi.com/trade-api/v2/series/"
                     f"{KALSHI_SERIES}/markets/{ticker}/candlesticks",
                     {"start_ts": start, "end_ts": now, "period_interval": 1440})
        except requests.RequestException:
            continue
        for c in cd.get("candlesticks", []):
            ts = c.get("end_period_ts")
            price = (c.get("price") or {}).get("close_dollars")
            if ts is None or price is None:
                continue
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            hist.append({"source": "kalshi", "candidate": name, "date": day,
                         "prob_pct": round(float(price) * 100, 2)})
        time.sleep(0.2)
    return snap, hist


# --- Manifold ---------------------------------------------------------------
def fetch_manifold(track: set[str] | None) -> tuple[list, list]:
    snap, hist = [], {}
    for slug in MANIFOLD_SLUGS:
        try:
            market = get(f"https://api.manifold.markets/v0/slug/{slug}")
        except requests.RequestException:
            continue
        answers = market.get("answers") or []
        if not answers:
            continue
        cid = market.get("id")
        ans_text = {a["id"]: a.get("text", "") for a in answers}
        for a in answers:
            name = a.get("text", "").strip()
            if not name or canonical(name) in GENERIC:
                continue
            snap.append({"source": "manifold", "candidate": name,
                         "prob_pct": round(a.get("probability", 0) * 100, 2),
                         "volume_usd": round(market.get("volume", 0), 2)})
        # Historique : rejouer les paris (probAfter par reponse, dernier du jour).
        try:
            bets = get("https://api.manifold.markets/v0/bets",
                       {"contractId": cid, "limit": 4000})
        except requests.RequestException:
            bets = []
        for b in sorted(bets, key=lambda x: x.get("createdTime", 0)):
            aid = b.get("answerId")
            prob = b.get("probAfter")
            name = ans_text.get(aid)
            if not name or prob is None or canonical(name) in GENERIC:
                continue
            day = datetime.fromtimestamp(b["createdTime"] / 1000,
                                         tz=timezone.utc).strftime("%Y-%m-%d")
            hist[(name, day)] = float(prob) * 100  # dernier du jour ecrase
        time.sleep(0.2)

    # Deduplique le snapshot (2 marches) : moyenne par candidat.
    snap_df = pd.DataFrame(snap)
    snap_out = []
    if not snap_df.empty:
        snap_df["key"] = snap_df["candidate"].map(canonical)
        for key, g in snap_df.groupby("key"):
            name = max(g["candidate"], key=len)
            snap_out.append({"source": "manifold", "candidate": name,
                             "prob_pct": round(g["prob_pct"].mean(), 2),
                             "volume_usd": round(g["volume_usd"].sum(), 2)})

    hist_out = [{"source": "manifold", "candidate": n, "date": d,
                 "prob_pct": round(p, 2)} for (n, d), p in hist.items()]
    if track is not None:
        hist_out = [h for h in hist_out if canonical(h["candidate"]) in track]
    return snap_out, hist_out


# --- Consensus --------------------------------------------------------------
def build_consensus(hist_df: pd.DataFrame) -> pd.DataFrame:
    """Consensus quotidien : par candidat, moyenne ponderee des sources
    (chaque source forward-fillee sur une grille journaliere)."""
    if hist_df.empty:
        return pd.DataFrame(columns=["date", "candidate", "prob_pct", "source"])
    hist_df = hist_df.copy()
    hist_df["key"] = hist_df["candidate"].map(canonical)
    start = pd.to_datetime(hist_df["date"]).min()
    end = pd.Timestamp(datetime.now(tz=timezone.utc).date())
    grid = pd.date_range(start, end, freq="D")

    rows = []
    for key, g in hist_df.groupby("key"):
        display = max(g["candidate"], key=len)
        per_source = {}
        for src, gs in g.groupby("source"):
            s = (gs.assign(date=pd.to_datetime(gs["date"]))
                   .drop_duplicates("date", keep="last")
                   .set_index("date")["prob_pct"].sort_index()
                   .reindex(grid).ffill())
            per_source[src] = s
        for d in grid:
            num = den = 0.0
            for src, s in per_source.items():
                v = s.loc[d]
                if pd.notna(v):
                    w = SOURCE_WEIGHTS.get(src, 1.0)
                    num += v * w
                    den += w
            if den > 0:
                rows.append({"date": d.strftime("%Y-%m-%d"), "candidate": display,
                             "prob_pct": round(num / den, 2), "source": "consensus"})
    return pd.DataFrame(rows)


def consensus_snapshot(snap_df: pd.DataFrame) -> pd.DataFrame:
    """Instantane consensus : moyenne ponderee des sources par candidat."""
    if snap_df.empty:
        return pd.DataFrame()
    df = snap_df.copy()
    df["key"] = df["candidate"].map(canonical)
    df["w"] = df["source"].map(lambda s: SOURCE_WEIGHTS.get(s, 1.0))
    rows = []
    for key, g in df.groupby("key"):
        display = max(g["candidate"], key=len)
        wsum = g["w"].sum()
        prob = (g["prob_pct"] * g["w"]).sum() / wsum if wsum else g["prob_pct"].mean()
        rows.append({"source": "consensus", "candidate": display,
                     "prob_pct": round(prob, 2),
                     "volume_usd": round(g["volume_usd"].sum(), 2),
                     "n_sources": g["source"].nunique()})
    return pd.DataFrame(rows).sort_values("prob_pct", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-prob", type=float, default=1.5,
                    help="Proba minimale (%%, dans une source) pour tracer l'historique")
    ap.add_argument("--out-history", default="data/markets.csv")
    ap.add_argument("--out-consensus", default="data/markets_consensus.csv")
    ap.add_argument("--out-snapshot", default="data/markets_snapshot.csv")
    args = ap.parse_args()

    fetchers = [("polymarket", fetch_polymarket),
                ("kalshi", fetch_kalshi),
                ("manifold", fetch_manifold)]

    # 1er passage : snapshots pour determiner les candidats a tracer.
    snapshots, live = [], {}
    for name, fn in fetchers:
        try:
            snap, _ = fn(track=set())  # set() vide -> pas d'historique ce tour
            snapshots.extend(snap)
            live[name] = True
            print(f"{name}: {len(snap)} candidats cotes.")
        except Exception as exc:  # une source down ne bloque pas les autres
            print(f"{name}: ECHEC ({exc})", file=sys.stderr)
            live[name] = False

    snap_df = pd.DataFrame(snapshots)
    if snap_df.empty:
        print("Aucune source disponible.", file=sys.stderr)
        return 1

    # Candidats a tracer dans l'historique : proba >= seuil dans au moins une source.
    tracked = {canonical(r["candidate"]) for r in snapshots
               if r["prob_pct"] >= args.min_prob}

    # 2e passage : historique pour les candidats retenus.
    hist_rows = []
    for name, fn in fetchers:
        if not live.get(name):
            continue
        try:
            _, hist = fn(track=tracked)
            hist_rows.extend(hist)
            print(f"{name}: {len(hist)} points d'historique.")
        except Exception as exc:
            print(f"{name}: historique indisponible ({exc})", file=sys.stderr)

    hist_df = pd.DataFrame(hist_rows)
    if not hist_df.empty:  # borne temporelle commune a toutes les sources
        hist_df = hist_df[hist_df["date"] >= HISTORY_START].reset_index(drop=True)
    consensus = build_consensus(hist_df)

    out_hist = Path(args.out_history)
    out_hist.parent.mkdir(parents=True, exist_ok=True)
    hist_df.sort_values(["candidate", "source", "date"]).to_csv(
        out_hist, index=False, encoding="utf-8")
    consensus.sort_values(["candidate", "date"]).to_csv(
        Path(args.out_consensus), index=False, encoding="utf-8")

    # Snapshot : par source + consensus.
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    snap_df["date"] = today
    csnap = consensus_snapshot(snap_df)
    csnap["date"] = today
    full_snap = pd.concat([snap_df.drop(columns=[c for c in ("key",) if c in snap_df],
                                        errors="ignore"), csnap], ignore_index=True)
    full_snap.to_csv(Path(args.out_snapshot), index=False, encoding="utf-8")

    n_sources = sum(1 for v in live.values() if v)
    print(f"OK : {n_sources} sources, historique {out_hist} ({len(hist_df)} points), "
          f"consensus {args.out_consensus} ({len(consensus)} points).")
    if not csnap.empty:
        top = csnap.head(5)
        print("Consensus du jour : " + " ; ".join(
            f"{r.candidate} {r.prob_pct:.1f}%" for r in top.itertuples()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
