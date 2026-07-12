#!/usr/bin/env python3
"""Scraper des sondages pour l'election presidentielle francaise de 2027.

Source : page Wikipedia "Opinion polling for the 2027 French presidential
election", qui agrege les sondages des principaux instituts (IFOP, IPSOS,
OpinionWay, Elabe, Odoxa, Harris, BVA, Cluster17, Toluna...).

Le script parcourt chaque section de la page, parse les tableaux de sondages
et produit un CSV "tidy" (une ligne par couple sondage/candidat), pratique
pour l'analyse. L'historique est assure par les commits git successifs.

Usage :
    python scrape_polls.py                 # ecrit data/polls.csv
    python scrape_polls.py --out chemin    # sortie personnalisee
    python scrape_polls.py --lang fr       # utilise la Wikipedia francaise
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# Wikipedia demande un User-Agent descriptif avec un contact.
USER_AGENT = (
    "presi-2027-poll-scraper/1.0 "
    "(https://github.com/sbbb-git/presi; scraping open election polling data)"
)

WIKI_PAGES = {
    "en": "Opinion polling for the 2027 French presidential election",
    "fr": "Liste de sondages sur l'election presidentielle francaise de 2027",
}

# --- Perimetre du suivi (choix produit) --------------------------------------
# On ne garde que le 1er et le 2nd tour ; les sections de scenarios
# hypothetiques (candidats generiques, rejeu de 2022...) sont exclues.
SECTIONS_KEEP = ("first round", "second round", "premier tour", "second tour")

# Le filtrage des candidats est pilote par le registre candidates.csv.
# Statuts nuances (les anciens candidate/not_candidate/undeclared restent lus) :
#   - declared   -> candidature annoncee explicitement (source datee)
#   - likely     -> intention forte affirmee, annonce formelle pas encore faite
#   - undecided  -> flou entretenu / pas de position claire
#   - withdrawn  -> a explicitement renonce ou s'est rallie a un autre candidat
#   - ineligible -> legalement empeche (justice, limite de mandats)
# Effet filtre : withdrawn/ineligible/not_candidate -> colonnes retirees et
# hypotheses dependantes ecartees ; --declared-only n'accepte que declared.
#   - required = TRUE -> doit figurer dans une hypothese pour la garder
DEFAULT_REGISTRY_PATH = "candidates.csv"

# Formes normalisees des statuts (normalise() supprime le "_").
DECLARED_STATUSES = {"declared", "candidate"}
EXCLUDED_STATUSES = {"withdrawn", "ineligible", "notcandidate"}
# -----------------------------------------------------------------------------

# Libelles de colonnes qui ne sont PAS des candidats/partis. Compares en
# minuscules, sans accents ni ponctuation, par egalite ou prefixe de MOT
# (jamais par sous-chaine : "n" ou "date" ne doivent pas matcher un nom).
# Les colonnes institut/date/echantillon sont deja detectees separement ;
# il reste surtout les colonnes agregees (abstention, avance, autres...).
META_COLUMN_KEYWORDS = {
    "polling firm", "pollster", "polling firmlink", "fieldwork",
    "fieldwork date", "date", "dates", "sample", "sample size", "size",
    "abstention", "abstentions", "turnout", "lead", "others", "other",
    "ref", "reference", "references", "link", "commanditaire", "institut",
    "echantillon", "sondeur", "avance", "autres", "periode", "marge",
    "date de publication",
}


def fetch_page_html(page_title: str, lang: str) -> str:
    """Recupere le HTML rendu d'une page Wikipedia via l'API action=parse."""
    endpoint = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text",
        "formatversion": "2",
        "format": "json",
        "redirects": "1",
    }
    resp = requests.get(
        endpoint, params=params, headers={"User-Agent": USER_AGENT}, timeout=60
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Erreur API Wikipedia : {data['error'].get('info')}")
    return data["parse"]["text"]


def normalise(text: str) -> str:
    """Minuscule, sans accents, sans ponctuation ni references [1]."""
    text = re.sub(r"\[.*?\]", "", text)  # notes de bas de page
    text = text.lower().strip()
    accents = str.maketrans("aaaeeeeiioouuuc", "aaaeeeeiioouuuc")
    text = (
        text.replace("é", "e").replace("è", "e").replace("ê", "e")
        .replace("ë", "e").replace("à", "a").replace("â", "a")
        .replace("î", "i").replace("ï", "i").replace("ô", "o")
        .replace("û", "u").replace("ù", "u").replace("ç", "c")
    )
    text = text.translate(accents)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Registry:
    """Suivi des candidatures, charge depuis candidates.csv.

    Fournit le statut (candidate / not_candidate / undeclared) d'une personne
    a partir d'un libelle de colonne Wikipedia, ainsi que la liste des
    personnes "requises" (qui doivent apparaitre dans une hypothese)."""

    def __init__(self, rows: list[dict]):
        self.by_key: dict[str, dict] = {}
        for row in rows:
            key = normalise(row.get("wiki_key", ""))
            if key:
                self.by_key[key] = row
        self.required_keys = {
            k for k, r in self.by_key.items()
            if str(r.get("required", "")).strip().upper() in {"TRUE", "1", "YES", "OUI"}
        }
        self.excluded_keys = {
            k for k, r in self.by_key.items()
            if normalise(r.get("status", "")) in EXCLUDED_STATUSES
        }

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        p = Path(path)
        if not p.exists():
            print(f"Avertissement : registre introuvable ({p}), aucun filtre "
                  "candidat applique.", file=sys.stderr)
            return cls([])
        df = pd.read_csv(p, dtype=str).fillna("")
        return cls(df.to_dict("records"))

    def match_key(self, column_label: str) -> str | None:
        """Trouve la cle de registre correspondant a un libelle de colonne."""
        n = normalise(column_label)
        if not n:
            return None
        if n in self.by_key:
            return n
        # Correspondance par sous-chaine (ex. "le pen (rn)" -> "le pen").
        for key in self.by_key:
            if key and (key in n or n in key):
                return key
        return None

    def status(self, column_label: str) -> str:
        key = self.match_key(column_label)
        if key is None:
            return "unknown"
        return normalise(self.by_key[key].get("status", "")) or "unknown"

    def display_name(self, column_label: str) -> str | None:
        key = self.match_key(column_label)
        return self.by_key[key].get("display_name") if key else None


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Aplati un MultiIndex de colonnes en gardant le libelle le plus parlant."""
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for tup in df.columns:
            parts = [
                str(p) for p in tup
                if p is not None and not str(p).startswith("Unnamed")
            ]
            flat.append(parts[-1] if parts else str(tup[-1]))
        df = df.copy()
        df.columns = flat
    else:
        df = df.copy()
        df.columns = [str(c) for c in df.columns]
    # Nettoie les references [1] dans les entetes.
    df.columns = [re.sub(r"\[.*?\]", "", c).strip() for c in df.columns]
    return df


def parse_percentage(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"\[.*?\]", "", s)  # notes
    s = s.replace("%", "").replace(",", ".").strip()
    # Un candidat en tete est parfois marque, on garde juste le nombre.
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_sample_size(value) -> int | None:
    if value is None:
        return None
    s = re.sub(r"[^0-9]", "", str(value))
    return int(s) if s else None


def parse_end_date(raw: str) -> str | None:
    """Extrait une date ISO (fin de terrain) depuis un libelle du type
    "29 Jun-1 Jul 2026" ou "1-3 juillet 2026". Best-effort ; renvoie None
    si l'analyse echoue."""
    if not raw:
        return None
    raw = re.sub(r"\[.*?\]", "", str(raw)).strip()
    # Prend le dernier segment apres un tiret (la fin de la periode).
    tail = re.split(r"[–—-]", raw)[-1].strip()
    # Complete l'annee/mois manquants a partir du libelle complet.
    year = re.search(r"\b(20\d{2})\b", raw)
    for candidate in (tail, raw):
        try:
            dt = dateparser.parse(candidate, dayfirst=True, fuzzy=True)
            if year:
                dt = dt.replace(year=int(year.group(1)))
            return dt.date().isoformat()
        except (ValueError, OverflowError):
            continue
    return None


def find_column(columns: list[str], *keywords: str) -> str | None:
    for col in columns:
        n = normalise(col)
        if any(k in n for k in keywords):
            return col
    return None


def is_meta_column(col: str) -> bool:
    n = normalise(col)
    if not n:
        return True
    # Egalite exacte, ou prefixe suivi d'une frontiere de mot. Jamais de
    # sous-chaine libre (sinon "date" matcherait un nom, etc.).
    for kw in META_COLUMN_KEYWORDS:
        if n == kw or n.startswith(kw + " "):
            return True
    return False


def parse_table(table_html: str, section: str, subsection: str, hypothesis_id: int,
                scraped_at: str, registry: Registry,
                apply_filters: bool, declared_only: bool) -> list[dict]:
    """Transforme un tableau de sondages en lignes tidy.

    Le filtrage est pilote par le registre :
      - on retire les colonnes des personnes declarees NON candidates ;
      - une hypothese est ecartee si une personne "requise" est absente ;
      - avec declared_only, une hypothese testant une personne non declaree
        candidate (statut undeclared/unknown) est egalement ecartee.
    """
    try:
        frames = pd.read_html(io.StringIO(table_html))
    except ValueError:
        return []
    if not frames:
        return []
    df = flatten_columns(frames[0])
    df = df.dropna(how="all")
    if df.empty:
        return []

    cols = list(df.columns)
    pollster_col = find_column(cols, "polling firm", "pollster", "institut", "sondeur")
    date_col = find_column(cols, "fieldwork", "date", "periode")
    sample_col = find_column(cols, "sample", "echantillon")

    # Sans institut identifiable, ce n'est pas un tableau de sondages.
    if pollster_col is None:
        return []

    candidate_cols = [
        c for c in cols
        if c not in {pollster_col, date_col, sample_col} and not is_meta_column(c)
    ]

    if apply_filters:
        # 1) Retire les colonnes des personnes retirees / ineligibles.
        candidate_cols = [
            c for c in candidate_cols
            if registry.status(c) not in EXCLUDED_STATUSES
        ]
        # 2) Exige la presence de toutes les personnes "requises".
        present_keys = {registry.match_key(c) for c in candidate_cols}
        present_keys.discard(None)
        if not registry.required_keys.issubset(present_keys):
            return []
        # 3) declared_only : aucune personne non declaree candidate toleree.
        if declared_only:
            if any(registry.status(c) not in DECLARED_STATUSES
                   for c in candidate_cols):
                return []

    if not candidate_cols:
        return []

    # Composition de l'hypothese : liste des candidats testes dans ce tableau.
    hypothesis_candidates = ", ".join(sorted({
        registry.display_name(c) or re.sub(r"\[.*?\]", "", str(c)).strip()
        for c in candidate_cols
    }))

    rows: list[dict] = []
    for _, r in df.iterrows():
        pollster = str(r.get(pollster_col, "")).strip()
        pollster = re.sub(r"\[.*?\]", "", pollster).strip()
        if not pollster or normalise(pollster) in {"", "nan"}:
            continue
        # Ignore les lignes de resultats reels (ex. "2022 election").
        if re.search(r"\b(19|20)\d{2}\b.*election|election.*result", pollster, re.I):
            continue

        raw_date = str(r.get(date_col, "")) if date_col else ""
        raw_date = re.sub(r"\[.*?\]", "", raw_date).strip()
        end_date = parse_end_date(raw_date)
        sample = parse_sample_size(r.get(sample_col)) if sample_col else None

        for c in candidate_cols:
            pct = parse_percentage(r.get(c))
            if pct is None:
                continue
            candidate = re.sub(r"\[.*?\]", "", str(c)).strip()
            rows.append({
                "section": section,
                "scenario": subsection or section,
                "hypothesis_id": hypothesis_id,
                "hypothesis_candidates": hypothesis_candidates,
                "pollster": pollster,
                "fieldwork_raw": raw_date or None,
                "fieldwork_end": end_date,
                "sample_size": sample,
                "candidate": registry.display_name(c) or candidate,
                "candidate_status": registry.status(c),
                "percentage": pct,
                "scraped_at": scraped_at,
            })
    return rows


def section_kept(section: str, all_sections: bool) -> bool:
    if all_sections:
        return True
    n = normalise(section)
    return any(k in n for k in SECTIONS_KEEP)


def scrape(lang: str, registry: Registry, apply_filters: bool,
           declared_only: bool, all_sections: bool) -> pd.DataFrame:
    page_title = WIKI_PAGES[lang]
    html = fetch_page_html(page_title, lang)
    soup = BeautifulSoup(html, "lxml")
    content = soup.find("div", class_="mw-parser-output") or soup
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    section = ""
    subsection = ""
    hypothesis_id = 0
    all_rows: list[dict] = []

    for el in content.descendants:
        name = getattr(el, "name", None)
        if name in ("h2", "h3", "h4"):
            heading = el.get_text(" ", strip=True)
            heading = re.sub(r"\[.*?\]|\bedit\b", "", heading).strip()
            if name == "h2":
                section = heading
                subsection = ""
            else:
                subsection = heading
        elif name == "table" and "wikitable" in (el.get("class") or []):
            if not section_kept(section, all_sections):
                continue
            hypothesis_id += 1
            rows = parse_table(str(el), section, subsection, hypothesis_id,
                               scraped_at, registry, apply_filters, declared_only)
            all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["hypothesis_id", "pollster", "fieldwork_raw", "candidate",
                    "percentage"]
        )
        df = df.sort_values(
            ["section", "hypothesis_id", "fieldwork_end", "pollster", "candidate"],
            na_position="last",
        ).reset_index(drop=True)
    return df


def build_candidates_seen(df: pd.DataFrame) -> pd.DataFrame:
    """Synthese des candidats reellement testes dans les sondages retenus,
    avec leur statut declare. Aide a maintenir candidates.csv a jour."""
    if df.empty:
        return pd.DataFrame(
            columns=["candidate", "status", "n_polls", "last_seen"])
    g = (
        df.groupby(["candidate", "candidate_status"])
        .agg(n_polls=("pollster", "size"),
             last_seen=("fieldwork_end", "max"))
        .reset_index()
        .rename(columns={"candidate_status": "status"})
        .sort_values(["status", "n_polls"], ascending=[True, False])
    )
    return g


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/polls.csv", help="Fichier CSV de sortie")
    ap.add_argument("--lang", default="en", choices=sorted(WIKI_PAGES),
                    help="Version linguistique de Wikipedia (en par defaut)")
    ap.add_argument("--candidates", default=DEFAULT_REGISTRY_PATH,
                    help="Registre des candidatures (defaut : candidates.csv)")
    ap.add_argument("--no-filter", action="store_true",
                    help="Ne pas filtrer selon le registre (garde tout)")
    ap.add_argument("--declared-only", action="store_true",
                    help="Ne garder que les hypotheses ou TOUS les candidats "
                         "testes sont declares candidats")
    ap.add_argument("--all-sections", action="store_true",
                    help="Inclure aussi les scenarios hypothetiques "
                         "(par defaut : 1er et 2nd tour uniquement)")
    ap.add_argument("--seen-out", default="data/candidates_seen.csv",
                    help="Synthese des candidats testes (aide au suivi)")
    ap.add_argument("--fallback-lang", default=None, choices=sorted(WIKI_PAGES),
                    help="Version de secours si la version primaire ne renvoie "
                         "rien (ex. --fallback-lang fr)")
    args = ap.parse_args()

    registry = Registry.load(args.candidates)
    print(f"Scraping des sondages 2027 depuis Wikipedia ({args.lang})...")
    if not args.no_filter and registry.required_keys:
        req = ", ".join(sorted(registry.required_keys))
        print(f"Filtre : hypotheses devant inclure [{req}], "
              f"exclusion des non-candidats.")

    def run(lang):
        return scrape(lang, registry,
                      apply_filters=not args.no_filter,
                      declared_only=args.declared_only,
                      all_sections=args.all_sections)

    df = run(args.lang)
    if df.empty and args.fallback_lang and args.fallback_lang != args.lang:
        print(f"Version {args.lang} vide, essai du fallback "
              f"{args.fallback_lang}...", file=sys.stderr)
        df = run(args.fallback_lang)

    if df.empty:
        print("Aucune donnee extraite. La structure de la page a peut-etre "
              "change, ou le filtre est trop strict.", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8")

    seen = build_candidates_seen(df)
    seen_out = Path(args.seen_out)
    seen_out.parent.mkdir(parents=True, exist_ok=True)
    seen.to_csv(seen_out, index=False, encoding="utf-8")

    n_polls = df.drop_duplicates(["scenario", "pollster", "fieldwork_raw"]).shape[0]
    print(f"OK : {len(df)} lignes, ~{n_polls} sondages, "
          f"{df['pollster'].nunique()} instituts, "
          f"{df['candidate'].nunique()} candidats.")
    print(f"Ecrit dans {out}")
    print(f"Suivi candidats vus dans {seen_out}")
    # Signale les candidats testes mais absents/undeclared du registre.
    unknown = seen[seen["status"].isin(["unknown", "undeclared"])]["candidate"].tolist()
    if unknown:
        print(f"A verifier dans candidates.csv (statut a trancher) : "
              f"{', '.join(unknown[:15])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
