# Sources de données — audit et choix

*Audit réalisé le 7-8 juillet 2026 par recherche web (chaque source inspectée
réellement : fraîcheur, format, exhaustivité des instituts, détail des
hypothèses de 1er tour, licence, faisabilité du scraping).*

## Sondages (intentions de vote)

| Source | Score /10 | Format | 2027 ? | Détail hypothèses | Décision |
|---|---|---|---|---|---|
| **Wikipédia EN** | 7,0 | tableaux HTML | ✅ | ✅ | **Retenue (primaire)** — structure régulière, déjà en place |
| Wikipédia FR | 7,5 | tableaux HTML | ✅ | ✅ (+ 47 liens PDF officiels) | Recoupement / fallback |
| Commission des sondages | 7,0 | PDF | ✅ | ✅ (exhaustif par la loi) | Chantier futur (parsing PDF) |
| Sites des instituts | 4,5 | mixte (HTML/PDF) | ✅ | partiel | Écartée (fragile, multi-format) |
| Politico Poll of Polls | 2,0 | JSON | ❌ | ❌ | Écartée (pas de page France 2027) |
| NSPpolls | 1,5 | JSON (MIT) | ❌ | ✅ (schéma idéal) | **Morte** — dernier sondage 2022, repo abandonné |
| Europe Elects | 1,5 | autre | ❌ | ❌ | Écartée |

**Choix** : Wikipédia EN reste la source primaire des sondages (parser
`scrape_polls.py`), avec un fallback FR léger. NSPpolls, malgré un format JSON
idéal, est inutilisable (aucune donnée depuis avril 2022). La Commission des
sondages (registre officiel exhaustif) est le meilleur candidat pour une future
3ᵉ source de validation, au prix d'un parsing PDF.

## Marchés prédictifs (probabilité de victoire)

| Source | Argent réel | API + historique | Liquidité | Poids consensus | Décision |
|---|---|---|---|---|---|
| **Polymarket** | ✅ (crypto) | ✅ | ~110 M$ | 1.0 | **Retenue** |
| **Kalshi** | ✅ (régulé CFTC) | ✅ | forte (200k+ contrats) | 1.0 | **Retenue** |
| **Manifold** | ❌ (play money) | ✅ | 150+ parieurs | 0.5 | **Retenue (appoint)** |
| PredictIt | ✅ | ✅ | quasi-morte | — | Écartée (prix fossiles) |
| Smarkets | ✅ | carnet, pas d'historique | ~3 500 £ | — | Écartée (illiquide, sans historique) |
| Insight / Zeitgeist / Augur | — | — | — | — | Écartées (pas de marché France) |

**Choix** : `scrape_markets.py` croise Polymarket + Kalshi + Manifold et calcule
un **consensus** (moyenne pondérée, Manifold à demi-poids car play money). Les
trois plateformes ont réagi au verdict Le Pen du 7 juillet 2026, ce qui valide
leur fraîcheur. Le croisement corrige les biais d'une plateforme isolée
(ex. contrats fins et sur-cotés sur Kalshi lissés par le consensus).

## Suivi des candidatures

Le statut de candidature (`candidates.csv`) est établi par recherche web
multi-sources + contre-vérification adversariale, et documenté dans
[`declarations.md`](declarations.md). Ce n'est pas une source de données
automatisée mais un registre maintenu à la main, réactualisé à la demande.
