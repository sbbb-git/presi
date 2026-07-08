#!/usr/bin/env python3
"""Collecte des probabilites implicites du marche predictif Polymarket
pour l'election presidentielle francaise de 2027.

Deux sorties :
  - data/markets.csv          : historique quotidien (tidy) par candidat
  - data/markets_snapshot.csv : instantane du jour (proba, volume)

Le signal est complementaire des sondages : il mesure une probabilite de
VICTOIRE (pas des intentions de vote) et reagit en continu a l'actualite.

API publiques utilisees (sans authentification) :
  - https://gamma-api.polymarket.com/events?slug=...   (marches + prix du jour)
  - https://clob.polymarket.com/prices-history          (historique par token)

Usage :
    python scrape_markets.py                      # defaut : evenement 2027
    python scrape_markets.py --min-prob 0.5       # seuil d'inclusion historique
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

GAMMA_URL = "https://gamma-api.polymarket.com/events"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
DEFAULT_SLUG = "next-french-presidential-election"
USER_AGENT = (
    "presi-2027-poll-scraper/1.0 "
    "(https://github.com/sbbb-git/presi; open election data project)"
)


def fetch_event(slug: str) -> dict:
    resp = requests.get(
        GAMMA_URL, params={"slug": slug},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    events = resp.json()
    if not events:
        raise RuntimeError(f"Aucun evenement Polymarket pour slug={slug!r}")
    return events[0]


def extract_candidates(event: dict) -> list[dict]:
    """Un marche binaire par candidat -> nom, proba (prix YES), volume, token."""
    out = []
    for m in event.get("markets", []):
        name = (m.get("groupItemTitle") or m.get("question") or "").strip()
        if not name:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except (TypeError, ValueError):
            continue
        if not prices or not tokens:
            continue
        try:
            prob = float(prices[0])  # prix du YES = proba implicite
        except (TypeError, ValueError):
            continue
        volume = m.get("volumeNum") or m.get("volume") or 0
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            volume = 0.0
        out.append({
            "candidate": name,
            "prob": prob,
            "volume_usd": volume,
            "yes_token": str(tokens[0]),
            "closed": bool(m.get("closed")),
        })
    out.sort(key=lambda c: -c["prob"])
    return out


def fetch_history(token: str, fidelity_minutes: int = 1440) -> list[dict]:
    resp = requests.get(
        CLOB_HISTORY_URL,
        params={"market": token, "interval": "max", "fidelity": fidelity_minutes},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("history", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default=DEFAULT_SLUG,
                    help="Slug de l'evenement Polymarket")
    ap.add_argument("--min-prob", type=float, default=0.5,
                    help="Proba minimale (en %%) pour recuperer l'historique")
    ap.add_argument("--out-history", default="data/markets.csv")
    ap.add_argument("--out-snapshot", default="data/markets_snapshot.csv")
    args = ap.parse_args()

    print(f"Recuperation du marche Polymarket ({args.slug})...")
    event = fetch_event(args.slug)
    candidates = extract_candidates(event)
    if not candidates:
        print("Aucun candidat cote trouve.", file=sys.stderr)
        return 1

    snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Instantane du jour -------------------------------------------------
    snap = pd.DataFrame([{
        "date": snapshot_date,
        "candidate": c["candidate"],
        "prob_pct": round(c["prob"] * 100, 2),
        "volume_usd": round(c["volume_usd"], 2),
        "closed": c["closed"],
        "source": "polymarket",
    } for c in candidates])
    out_snap = Path(args.out_snapshot)
    out_snap.parent.mkdir(parents=True, exist_ok=True)
    snap.to_csv(out_snap, index=False, encoding="utf-8")

    # --- Historique quotidien (candidats au-dessus du seuil) ----------------
    keep = [c for c in candidates
            if c["prob"] * 100 >= args.min_prob and not c["closed"]]
    print(f"{len(candidates)} candidats cotes ; historique pour {len(keep)} "
          f"(proba >= {args.min_prob}%).")

    rows = []
    for c in keep:
        try:
            history = fetch_history(c["yes_token"])
        except requests.RequestException as exc:
            print(f"  ! {c['candidate']}: {exc}", file=sys.stderr)
            continue
        # Un point par jour : on garde le dernier releve de chaque date UTC.
        by_day: dict[str, float] = {}
        for p in history:
            day = datetime.fromtimestamp(p["t"], tz=timezone.utc).strftime("%Y-%m-%d")
            by_day[day] = float(p["p"])
        for day, prob in sorted(by_day.items()):
            rows.append({
                "date": day,
                "candidate": c["candidate"],
                "prob_pct": round(prob * 100, 2),
                "source": "polymarket",
            })
        time.sleep(0.3)  # politesse API

    if not rows:
        print("Aucun historique recupere.", file=sys.stderr)
        return 1

    hist = pd.DataFrame(rows).sort_values(["candidate", "date"])
    out_hist = Path(args.out_history)
    out_hist.parent.mkdir(parents=True, exist_ok=True)
    hist.to_csv(out_hist, index=False, encoding="utf-8")

    top = snap.head(5)
    print(f"Snapshot : {out_snap} ({len(snap)} candidats). "
          f"Historique : {out_hist} ({len(hist)} points).")
    print("Top 5 du jour : " + " ; ".join(
        f"{r.candidate} {r.prob_pct:.1f}%" for r in top.itertuples()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
