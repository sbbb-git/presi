# Sondages — élection présidentielle française 2027

Scraper hebdomadaire / semi-hebdomadaire des intentions de vote pour l'élection
présidentielle de 2027, à partir de la page Wikipédia agrégée
[*Opinion polling for the 2027 French presidential election*](https://en.wikipedia.org/wiki/Opinion_polling_for_the_2027_French_presidential_election),
qui compile les sondages des principaux instituts (Ifop, Ipsos, OpinionWay,
Elabe, Odoxa, Harris Interactive, BVA, Cluster17, Toluna…).

## Ce que fait le scraper

- Parcourt les sections **Premier tour** et **Second tour** de la page (les
  scénarios purement hypothétiques sont exclus par défaut).
- Produit un CSV « tidy » : **une ligne par couple (sondage, candidat)**.
- Filtre les hypothèses selon un **registre de candidatures** maintenable
  (`candidates.csv`) : on ne garde que les configurations cohérentes avec qui
  s'est déclaré candidat / non-candidat.
- Tient un **suivi des candidats testés** (`data/candidates_seen.csv`) pour
  repérer les personnes dont le statut reste à trancher dans le registre.

L'historique des sondages est assuré par les **commits git successifs** : à
chaque exécution, `data/polls.csv` est réécrit et le diff git montre l'évolution.

## Le registre de candidatures — `candidates.csv`

C'est le fichier que **tu maintiens à la main** au fil des déclarations.

| Colonne | Rôle |
|---|---|
| `wiki_key` | Nom (nom de famille) tel qu'il apparaît dans les colonnes Wikipédia, ex. `le pen`, `melenchon` |
| `display_name` | Nom affiché dans les données |
| `party` | Parti |
| `status` | `candidate`, `not_candidate` ou `undeclared` |
| `required` | `TRUE` = doit figurer dans une hypothèse pour qu'elle soit conservée |
| `declared_on` | Date de la déclaration |
| `source` | Lien vers la source de la déclaration |
| `notes` | Commentaire libre |

Effet du `status` sur le filtrage :

- **`candidate`** → conservé normalement.
- **`not_candidate`** → ses colonnes sont retirées, et toute hypothèse qui en
  dépend est écartée. *Exemple actuel : Marine Le Pen étant candidate RN,
  Jordan Bardella est `not_candidate`, donc les sondages le testant comme
  candidat RN sont ignorés.*
- **`undeclared`** → conservé tel quel (les instituts le testent encore).

`required = TRUE` filtre **par hypothèse** : seules les configurations
contenant toutes les personnes requises sont gardées (par défaut, Marine
Le Pen).

### Mettre à jour une candidature

Quand quelqu'un se déclare (ou se retire), édite la ligne correspondante :

```csv
melenchon,Jean-Luc Melenchon,La France insoumise,candidate,FALSE,2026-09-01,https://...,
```

Puis relance le scraper. Si un nom apparaît dans les sondages mais pas dans le
registre, il est signalé en fin d'exécution (statut `unknown`).

## Utilisation

```bash
pip install -r requirements.txt

# Exécution standard (1er + 2nd tour, filtre du registre) -> data/polls.csv
python scrape_polls.py

# N'inclure que les hypothèses où TOUS les candidats testés sont déclarés
python scrape_polls.py --declared-only

# Tout récupérer sans filtre (debug), scénarios inclus
python scrape_polls.py --no-filter --all-sections

# Source : Wikipédia française
python scrape_polls.py --lang fr
```

### Options principales

| Option | Effet |
|---|---|
| `--out CHEMIN` | Fichier CSV de sortie (défaut `data/polls.csv`) |
| `--candidates CHEMIN` | Registre de candidatures (défaut `candidates.csv`) |
| `--declared-only` | Ne garder que les hypothèses 100 % candidats déclarés |
| `--all-sections` | Inclure aussi les scénarios hypothétiques |
| `--no-filter` | Désactiver tout filtrage lié au registre |
| `--lang {en,fr}` | Version linguistique de Wikipédia (défaut `en`) |

## Colonnes de `data/polls.csv`

| Colonne | Description |
|---|---|
| `section` | `First round` / `Second round` |
| `scenario` | Sous-section de la page (période, duel…) |
| `hypothesis_id` | Identifiant du tableau/hypothèse (candidats testés ensemble) |
| `hypothesis_candidates` | Composition de l'hypothèse (liste des candidats du tableau) |
| `pollster` | Institut de sondage |
| `fieldwork_raw` | Dates de terrain (texte brut Wikipédia) |
| `fieldwork_end` | Date de fin de terrain (ISO, best-effort) |
| `sample_size` | Taille de l'échantillon |
| `candidate` | Candidat |
| `candidate_status` | Statut déclaré du candidat (issu du registre) |
| `percentage` | Intention de vote (%) |
| `scraped_at` | Date de récupération |

## Automatisation

Le workflow [`.github/workflows/scrape-polls.yml`](.github/workflows/scrape-polls.yml)
s'exécute **lundi et jeudi à 06:00 UTC**, relance le scraper et commit
automatiquement `data/` si les sondages ont changé. Lancement manuel possible
via l'onglet **Actions → Scraper sondages 2027 → Run workflow**.

## Limites

- Le scraper dépend de la structure de la page Wikipédia ; si elle change
  fortement, l'extraction peut nécessiter un ajustement.
- Les dates de terrain sont parsées au mieux (`fieldwork_end`), le texte brut
  reste disponible dans `fieldwork_raw`.
- Tant que les instituts testent surtout Jordan Bardella comme candidat RN,
  peu d'hypothèses avec Marine Le Pen sont disponibles ; le jeu de données
  s'étoffera à mesure que les sondages l'intègrent.
