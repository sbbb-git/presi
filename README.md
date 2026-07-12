# Sondages & marchés — élection présidentielle française 2027

Suivi hebdomadaire / semi-hebdomadaire de l'élection présidentielle de 2027,
croisant **deux signaux** :

1. **Intentions de vote** (sondages) — page Wikipédia agrégée
   [*Opinion polling for the 2027 French presidential election*](https://en.wikipedia.org/wiki/Opinion_polling_for_the_2027_French_presidential_election)
   (Ifop, Ipsos, OpinionWay, Elabe, Odoxa, Harris, Cluster17…).
2. **Probabilités de victoire** (marchés prédictifs) — consensus de
   **Polymarket + Kalshi + Manifold**.

Un [dashboard](docs/index.html) affiche les deux, plus le suivi des candidatures.
Audit détaillé des sources : [`SOURCES.md`](SOURCES.md). Note d'analyse des
dynamiques : [`analyse.md`](analyse.md).

## Ce que fait le scraper de sondages

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

## Le collecteur de marchés — `scrape_markets.py`

Croise trois marchés prédictifs (API publiques, sans authentification) et calcule
un **consensus** pondéré (Polymarket 1.0, Kalshi 1.0, Manifold 0.5 — play money) :

- `data/markets.csv` — historique quotidien par (source, candidat)
- `data/markets_consensus.csv` — consensus quotidien (moyenne pondérée)
- `data/markets_snapshot.csv` — instantané du jour par plateforme + consensus

Le signal mesure une probabilité de **victoire** (pas une intention de vote) et
réagit à l'actualité en continu. Le croisement corrige les biais d'une plateforme
isolée (contrats fins/sur-cotés lissés par le consensus). Chaque source est
isolée : si une plateforme tombe, les autres passent.

## Le registre de candidatures — `candidates.csv`

C'est le fichier que **tu maintiens à la main** au fil des déclarations.

| Colonne | Rôle |
|---|---|
| `wiki_key` | Nom (nom de famille) tel qu'il apparaît dans les colonnes Wikipédia, ex. `le pen`, `melenchon` |
| `display_name` | Nom affiché dans les données |
| `party` | Parti |
| `status` | `declared`, `likely`, `undecided`, `withdrawn` ou `ineligible` |
| `required` | `TRUE` = doit figurer dans une hypothèse pour qu'elle soit conservée |
| `declared_on` | Date de l'événement clé (annonce, renoncement, jugement) |
| `checked_on` | Date de la dernière vérification web du statut |
| `source` | URL de la source de presse la plus probante |
| `notes` | Commentaire libre |

Taxonomie des statuts (chacun sourcé, voir [`declarations.md`](declarations.md)) :

- **`declared`** → candidature annoncée explicitement et publiquement.
- **`likely`** → intention forte affirmée (candidat « quoi qu'il arrive »,
  investiture en cours) mais annonce formelle pas encore faite.
- **`undecided`** → flou entretenu, pas de position claire.
- **`withdrawn`** → a explicitement renoncé ou s'est rallié à un autre candidat.
- **`ineligible`** → légalement empêché (inéligibilité judiciaire, limite
  constitutionnelle de mandats).

Effet sur le filtrage des sondages :

- `withdrawn` / `ineligible` → colonnes retirées, hypothèses dépendantes écartées.
- `--declared-only` → n'accepte que les hypothèses composées de `declared`.
- Les autres statuts sont conservés tels quels (les instituts les testent encore).
- Les anciens statuts (`candidate`, `not_candidate`, `undeclared`) restent lus.

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

# 1) Sondages (1er + 2nd tour, filtre du registre) -> data/polls.csv
python scrape_polls.py

# 2) Marchés prédictifs (consensus 3 plateformes) -> data/markets*.csv
python scrape_markets.py

# 3) Dashboard -> docs/index.html
python build_site.py

# Variantes sondages :
python scrape_polls.py --declared-only              # hypothèses 100 % déclarés
python scrape_polls.py --no-filter --all-sections   # tout (debug)
python scrape_polls.py --lang fr                     # Wikipédia FR
python scrape_polls.py --fallback-lang fr            # EN, bascule FR si vide
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

## Dashboard web (GitHub Pages)

Un tableau de bord statique est généré dans [`docs/index.html`](docs/index.html)
par [`build_site.py`](build_site.py) à partir de `data/polls.csv` : courbes
d'évolution des intentions de vote au 1er tour (top candidats), suivi des
candidatures (déclarés / indécis / écartés) et tableau des dernières valeurs.
Page autonome, sans dépendance externe, thème clair/sombre automatique.

```bash
python build_site.py          # régénère docs/index.html depuis le CSV
```

### Activer GitHub Pages (une seule fois)

1. Repo **Settings → Pages**.
2. **Build and deployment → Source : GitHub Actions**.

Ensuite, le workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml)
publie automatiquement `docs/` à chaque mise à jour (ou via **Actions → Run
workflow**). L'URL sera `https://sbbb-git.github.io/presi/`.

> Le déploiement Pages se fait depuis la **branche par défaut** du dépôt. Tant
> que le travail vit sur une branche de feature, lance le déploiement à la main
> (`workflow_dispatch`) ou fusionne d'abord dans la branche par défaut.

## Automatisation

Le workflow [`.github/workflows/scrape-polls.yml`](.github/workflows/scrape-polls.yml)
s'exécute **lundi et jeudi à 06:00 UTC**, relance le scraper, régénère le
dashboard et commit automatiquement `data/` + `docs/` si les sondages ont
changé. Lancement manuel possible via l'onglet **Actions → Scraper sondages
2027 → Run workflow**. Le commit sur `docs/` déclenche à son tour la
publication Pages.

## Limites

- Le scraper dépend de la structure de la page Wikipédia ; si elle change
  fortement, l'extraction peut nécessiter un ajustement.
- Les dates de terrain sont parsées au mieux (`fieldwork_end`), le texte brut
  reste disponible dans `fieldwork_raw`.
- Tant que les instituts testent surtout Jordan Bardella comme candidat RN,
  peu d'hypothèses avec Marine Le Pen sont disponibles ; le jeu de données
  s'étoffera à mesure que les sondages l'intègrent.
