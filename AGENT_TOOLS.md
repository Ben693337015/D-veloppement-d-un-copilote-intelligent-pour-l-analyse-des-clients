# Contrat des outils du copilote — spécification pour le tool-calling (Semaine 2)

**Tâche #4 de la roadmap de clôture.** Ce document liste les fonctions que le LLM pourra
appeler à partir de la tâche #6 (`llm_client.py` + `tools=`). Chaque outil correspond
1-pour-1 à un endpoint backend déjà livré et testé (Semaine 1) — aucune nouvelle logique
métier à écrire en semaine 2, seulement les exposer comme outils (cf. tâche #7).

## 1. `get_kpis`

| | |
|---|---|
| **Endpoint** | `GET /api/v1/analytics/kpis` |
| **Paramètres** | aucun |
| **Retourne** | `nb_clients`, `nb_transactions`, `chiffre_affaires_total`, `stock_total_unites`, `solde_tresorerie_estime` |
| **Questions types** | "Combien j'ai de clients ?", "Quel est mon CA total ?", "Résume-moi la situation." |

## 2. `get_sales_forecast`

| | |
|---|---|
| **Endpoint** | `GET /api/v1/analytics/forecast/sales` |
| **Paramètres** | `horizon_jours` (int, 1-90, défaut 30) · `modele` (`prophet`\|`arima`\|`xgboost`, défaut `prophet`) |
| **Retourne** | `modele_utilise`, `points[]` (date/valeur/bornes), `rmse_validation`, `mae_validation`, `avertissements[]`, `explicabilite[]` (top 3 variables influentes — vide sauf `modele=xgboost`, cf. tâche #3) |
| **Questions types** | "Combien vais-je vendre le mois prochain ?", "Pourquoi cette prévision ?" (→ `explicabilite`) |
| **Note agent** | Si la question porte sur le "pourquoi" de la prévision, préférer `modele=xgboost` pour obtenir `explicabilite` non vide. |

## 3. `get_cashflow_forecast`

| | |
|---|---|
| **Endpoint** | `GET /api/v1/analytics/cashflow/forecast` |
| **Paramètres** | `horizon_jours` (int, 1-90, défaut 45) · `modele` (idem ci-dessus) · `seuil_critique` (float, défaut 0) |
| **Retourne** | `prevision` (même forme que `get_sales_forecast`, sans `explicabilite`) + `alerte` (`date_alerte`, `solde_projete`, `seuil_critique`, `niveau`: `ok`\|`vigilance`\|`risque_deficit`, `message`) |
| **Questions types** | "Quel est mon risque de trésorerie à 45 jours ?", "Vais-je être à découvert ?" |
| **Note agent** | `alerte.niveau` est calculé sur le pire jour de tout l'horizon, pas seulement la fin de période — le répercuter tel quel à l'utilisateur, ne pas se contenter du solde de fin d'horizon. |

## 4. `get_rfm_segments`

| | |
|---|---|
| **Endpoint** | `GET /api/v1/marketing/clients` |
| **Paramètres** | `segment` (str, optionnel — ex. `"À risque"`, `"Perdus"`, `"Champions"`) · `limit` (int, 1-1000, défaut 200) |
| **Retourne** | Liste de `ClientRFM` (client + `segment_rfm` + scores R/F/M) |
| **Questions types** | "Quels sont mes clients à risque ?", "Qui sont mes meilleurs clients ?" |
| **Note agent** | Pour une question de segment précis, passer `segment=<nom exact>` plutôt que filtrer soi-même une liste complète — le nom des segments doit être repris tel qu'affiché par `GET /marketing/segments/summary` (non exposé comme outil séparé dans ce périmètre minimal : l'agent peut s'appuyer sur les libellés de segment déjà connus via une première réponse de `get_rfm_segments`, ou l'utilisateur peut les nommer directement). |

## 5. `get_stock_alerts`

| | |
|---|---|
| **Endpoint** | `GET /api/v1/analytics/stock/alertes` |
| **Paramètres** | aucun |
| **Retourne** | Liste de `AlerteStock` (`code_produit`, `nom_produit`, `quantite_disponible`, `seuil_alerte`, `niveau`: `critique`\|`attention`\|`ok`) |
| **Questions types** | "Qu'est-ce qu'il faut réapprovisionner ?", "Quels produits sont en rupture ?" |

## 6. Cas sans outil

Certaines questions ne nécessitent aucun appel d'outil (salutations, questions générales sur
le fonctionnement du copilote, reformulations). Le golden dataset (tâche #9) doit inclure
quelques exemples de ce type pour vérifier que l'agent ne force pas un appel inutile.

## Notes de conception pour la tâche #6/#7

- Tous les outils sont des `GET` idempotents et sans effet de bord → aucun garde-fou de
  confirmation nécessaire côté agent (contrairement à un outil d'écriture, absent de ce
  périmètre).
- Authentification : tous ces endpoints exigent déjà un JWT (`get_current_user`). L'agent
  appelle les services Python directement (`analytics_service`, `forecasting_service`,
  `marketing_service`), pas les endpoints HTTP eux-mêmes — pas de jeton à gérer côté outil,
  la session utilisateur du copilote suffit.
- Paramètres invalides (ex. `horizon_jours=999`) : laisser FastAPI/Pydantic les rejeter avec
  un message clair (422), à faire remonter à l'utilisateur sans plantage (tâche #8).