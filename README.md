# Copilote IA PME — Groupe 2

**Master 1 Intelligence Artificielle et Applications — Université de Ngaoundéré**
Binôme : **Abdoulmadjid Ben Yahya** (segmentation RFM & marketing) · **Maslaw Garga Ibrahim** (ventes, trésorerie, stocks)

Plateforme de pilotage commercial pour PME : segmentation client (RFM + clustering), prévision
des ventes, suivi trésorerie/stocks et assistant conversationnel, le tout derrière une API
FastAPI unique et un frontend React.

## Sommaire

1. [Jeu de données](#1-jeu-de-données)
2. [Dépendances](#2-dépendances)
3. [Architecture](#3-architecture)
4. [Schéma PostgreSQL](#4-schéma-postgresql)
5. [Endpoints exposés](#5-endpoints-exposés)
6. [Choix des modèles & benchmarks](#6-choix-des-modèles--benchmarks)
7. [Installation](#7-installation) — [Docker](#7a-option-a--docker-recommandé) · [Locale](#7b-option-b--installation-locale-sans-docker)
8. [Tests & qualité](#8-tests--qualité)
9. [État d'avancement](#9-état-davancement)

---

## 1. Jeu de données

| Dataset | Usage | Lien |
|---|---|---|
| **Online Retail II** (UCI, ~1,07M lignes de transactions, licence CC BY 4.0) | Source de référence du module RFM (Abdoulmadjid) : `clients`, `transactions` | https://archive.ics.uci.edu/dataset/502/online+retail+ii |
| Miroir Kaggle du même dataset | Alternative de téléchargement si UCI est indisponible | https://www.kaggle.com/datasets/mashlyn/online-retail-ii-uci |
| **Store Sales — Time Series Forecasting** (Kaggle) | Référence pour le module de prévision des ventes (Maslaw) | https://www.kaggle.com/competitions/store-sales-time-series-forecasting |
| **AdventureWorksDW** (Microsoft, licence MIT, CSV) | Jeu complet ventes + stock + trésorerie prêt à importer, sans SQL Server | https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works/data-warehouse-install-script |
| **Wide World Importers** (Microsoft, licence MIT) | Jeu complet ventes + stock + trésorerie d'une même PME fictive, pour démontrer les deux modules | https://github.com/Microsoft/sql-server-samples/releases/tag/wide-world-importers-v1.0 |
| Génération simulée (Faker + NumPy) | `stocks_produits` et `tresorerie`, ancrée sur les volumes de vente réels déjà importés | `app/ingestion/simulate_tresorerie_stocks.py` |

Aucun fichier de données n'est versionné dans ce dépôt (`data/raw/` et `data/processed/` sont
ignorés par git, cf. `.gitignore`) — chaque membre télécharge le CSV depuis le lien ci-dessus et
le place dans `data/raw/` avant de lancer les scripts d'ingestion.

```bash
# 1. Segmentation RFM — charge Online Retail II dans clients/transactions
python -m app.ingestion.load_online_retail --source data/raw/online_retail_II.csv

# 2. Simulation ancrée sur les ventes réelles — stocks_produits/tresorerie
python -m app.ingestion.simulate_tresorerie_stocks
```

### Jeu complet prêt à l'emploi — AdventureWorks (Microsoft, recommandé)

Sans SQL Server : Microsoft publie l'entrepôt d'exemple AdventureWorksDW en fichiers CSV dans son
dépôt GitHub officiel (licence MIT). Une commande les télécharge et produit les trois fichiers
à importer depuis l'écran « Données » (ventes web de 18 484 clients 2010-2014, stock des 130
articles vendus, trésorerie mensuelle reconstituée depuis le grand livre) :

```bash
python -m app.ingestion.export_adventureworks     # -> data/adventureworks/{ventes,stock,tresorerie}.csv + LISEZMOI.md
```

Source : https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works/data-warehouse-install-script
Choix de transformation détaillés dans le `LISEZMOI.md` généré ; tests dans
`tests/test_export_adventureworks.py`.

### Variante — Wide World Importers (Microsoft, nécessite SQL Server dans Docker)

Online Retail II ne contient que des ventes. Pour alimenter **les trois onglets** de l'écran
« Données » avec une seule entreprise cohérente (mêmes codes produits en ventes et en stock),
on utilise la base d'exemple officielle **Wide World Importers** (Microsoft, licence MIT,
PME fictive de négoce, 2013-2016, données simulées) :
https://github.com/Microsoft/sql-server-samples/releases/tag/wide-world-importers-v1.0

```bash
pip install pymssql                                    # pilote SQL Server (outil ponctuel, hors requirements)
docker compose -f scripts/wwi/docker-compose.yml up -d # SQL Server éphémère
python -m app.ingestion.export_wwi --telecharger       # télécharge le .bak (~120 Mo), restaure, exporte
docker compose -f scripts/wwi/docker-compose.yml down -v
```

Résultat dans `data/wwi/` : `ventes.csv`, `stock.csv`, `tresorerie.csv` et `LISEZMOI.md`
(provenance, volumes, choix de transformation). Les noms de colonnes sont choisis pour que
l'écran « Données » propose seul le bon mapping ; le script le vérifie avec le vrai chargeur
de l'application. Importer dans l'ordre ventes -> stock -> trésorerie. Si `ventes.csv` dépasse
la limite d'upload de 50 Mo : `--depuis 2015-01-01`. Transformations testées sans SQL Server
dans `tests/test_export_wwi.py`.

### Import universel — n'importe quelle PME

Online Retail II sert de jeu de données de référence, mais **la plateforme n'est pas limitée à
ses noms de colonnes**. N'importe quelle PME peut importer son propre export (caisse, ERP,
tableur comptable) via l'API d'ingestion, commune aux deux sous-projets :

```
POST /api/v1/ingestion/preview
     upload du fichier (CSV/XLSX) -> l'API détecte l'encodage/séparateur et SUGGÈRE
     un mapping de colonnes pour les 3 tables (transactions, stocks_produits,
     tresorerie), sans jamais l'appliquer.

POST /api/v1/ingestion/import/transactions   (multipart: file + mapping JSON)
POST /api/v1/ingestion/import/stocks         (multipart: file + mapping JSON)
POST /api/v1/ingestion/import/tresorerie     (multipart: file + mapping JSON)
     import réel une fois le mapping vérifié/corrigé par l'utilisateur, avec rapport
     de qualité (lignes rejetées, doublons, clients créés...).
```

Exemple de mapping pour `/import/transactions` (colonnes arbitraires) :

```json
{
  "date_col": "Order Date",
  "client_col": "Client ID",
  "quantite_col": "Qty",
  "prix_unitaire_col": "Unit Price",
  "code_produit_col": "Product Code",
  "numero_facture_col": "Invoice Ref",
  "pays_col": "Country"
}
```

`app/ingestion/load_online_retail.py` est lui-même un simple appel à ce pipeline générique avec
un mapping figé (`ONLINE_RETAIL_MAPPING`) : Online Retail II est traité comme UN CAS PARTICULIER
d'import, pas comme un chemin de code séparé. Voir `app/ingestion/generic_loader.py`,
`generic_transactions.py`, `generic_stock.py`, `generic_tresorerie.py`.

**Segmentation RFM recalculée automatiquement à chaque import** — `POST
/import/transactions` déclenche désormais `rfm_clustering_service.run_full_pipeline`
(K-Means, paramètres par défaut) dès que des lignes ont réellement été importées :
`clients.segment_rfm`/`score_churn`/`cluster_id` ne restent donc jamais périmés en base
entre deux imports, sans action manuelle sur `POST /marketing/rfm/run` (cf.
`ingestion_service._rafraichir_segmentation_rfm`). Ce recalcul automatique reste sur
l'algorithme par défaut (kmeans) — un choix explicite de `dbscan`/`gmm` ou d'une autre
fenêtre d'analyse via `POST /marketing/rfm/run` reste possible à tout moment après. Un
échec du recalcul (jeu de données dégénéré) n'annule jamais l'import déjà commité : il est
signalé dans `avertissements` plutôt que renvoyé en erreur HTTP.

---

## 2. Dépendances

`requirements.txt` (audité par recherche exhaustive des imports — aucun paquet mort) :

| Catégorie | Paquets | Usage |
|---|---|---|
| Web / API | `fastapi`, `uvicorn[standard]`, `python-multipart` | Serveur HTTP, upload de fichiers |
| Base de données | `sqlalchemy`, `psycopg2-binary`, `alembic` | ORM, driver PostgreSQL, migrations |
| Validation / config | `pydantic`, `pydantic-settings`, `python-dotenv` | Schémas API, lecture de `.env` |
| Authentification | `bcrypt`, `python-jose[cryptography]` | Hachage de mot de passe, jetons JWT |
| Données / fichiers | `pandas`, `numpy`, `openpyxl`, `xlrd` | Pipeline d'ingestion (CSV/XLSX/XLS) |
| Machine Learning | `scikit-learn`, `statsmodels`, `prophet`, `xgboost`, `holidays` | Clustering RFM (K-Means/DBSCAN/GMM), prévision des ventes (ARIMA/Prophet/XGBoost) |
| LLM / Copilote | `httpx` | Appel HTTP direct vers l'API du fournisseur LLM configuré (pas de SDK dédié) |
| Rapport PDF | `weasyprint`, `matplotlib` | Génération HTML→PDF, graphiques |
| Tests | `pytest`, `httpx`, `faker` | Suite de tests, données simulées |

`frontend/package.json` — dépendances Node, indépendantes de l'écosystème Python :

| Paquets | Usage |
|---|---|
| `react`, `react-dom`, `react-router-dom` | Framework UI + navigation |
| `axios` | Appels HTTP vers l'API (`frontend/src/api/client.js`, seul point d'accès réseau) |
| `recharts` | Graphiques (coude/silhouette, segments, prévision des ventes) |
| `tailwindcss` (+ `@tailwindcss/vite`) | Système de design (`src/index.css`) |
| `vite` | Bundler / serveur de dev |
| `vitest`, `@testing-library/react` | Tests de rendu (dev only) |

**Dépendance système (Docker)** : `weasyprint` charge dynamiquement au runtime des bibliothèques
système (`glib`, `pango`, `harfbuzz`, `fontconfig`) non installées par `pip` seul — le `Dockerfile`
installe les paquets Debian correspondants. Sans eux, `POST /api/v1/report/pdf` échoue avec une
`OSError` au premier appel.

---

## 3. Architecture

```
                        ┌───────────────────────────┐
                        │  Navigateur de l'utilisateur │
                        └──────────────┬────────────┘
                                       │ HTTP (une seule origine)
                        ┌──────────────▼────────────┐
                        │  Frontend React (frontend/)  │
                        │  Nginx : sert le build +   │
                        │  reverse-proxy /api -> api │
                        │  (port 8501)               │
                        └──────────────┬────────────┘
                                       │ HTTP/JSON (frontend/src/api/client.js,
                                       │ seul point d'accès réseau du frontend)
                        ┌──────────────▼────────────┐
                        │        FastAPI (app/)      │
                        │  Routers → Schemas → Services → ORM │
                        ├───────────────────────────┤
                        │ /api/v1/auth/       (JWT)  │
                        │ /api/v1/analytics/  (Maslaw)│
                        │ /api/v1/marketing/  (Abdoulmadjid)│
                        │ /api/v1/copilot/chat (mutualisé)│
                        │ /api/v1/ingestion/  (mutualisé)│
                        └──────┬─────────────┬───────┘
                               │             │ HTTP (httpx), si une clé
                               │             │ est configurée (cf. §6) :
                     ┌─────────▼───┐   ┌─────▼──────────────┐
                     │ PostgreSQL   │   │ Anthropic / OpenAI / │
                     │ users        │   │ Groq / Google /      │
                     │ clients      │   │ OpenRouter            │
                     │ transactions │   └──────────────────────┘
                     │ stocks_produits│
                     │ tresorerie   │
                     └──────────────┘
```

**Pourquoi un reverse-proxy Nginx ?** Le frontend s'exécute dans le navigateur, pas côté serveur
— il ne peut jamais résoudre `api` (nom du service, valide uniquement à l'intérieur du réseau
Docker Compose). Le conteneur `frontend` sert donc le build statique **et** fait office de
reverse-proxy : le navigateur n'appelle qu'une seule origine (`localhost:3000`, port 8501 dans le conteneur), Nginx redirige
en interne `/api/*` vers `http://api:8000/api/*` (cf. `frontend/nginx.conf`). Bénéfice
secondaire : aucun problème de CORS.

**Principe des 4 couches** (Router → Schema → Service → ORM, cf. Cadrage §4.1) : un routeur ne
contient jamais de logique métier ni de requête SQL directe ; il délègue au service
correspondant, qui manipule les modèles ORM. Cela permet à Maslaw et Abdoulmadjid de faire
évoluer leurs modules sans se marcher dessus, tout en partageant `clients`/`transactions` comme
contrat d'interface commun.

### Arborescence

```
copilote-ia-pme/
├── README.md
├── CONTRIBUTING.md          # règles de branche/PR pour le binôme
├── Makefile                 # raccourcis: make up / test / lint / migrate...
├── pyproject.toml           # config ruff (lint) + pytest
├── docker-compose.yml       # orchestration PostgreSQL + API + Frontend
├── Dockerfile                # image API
├── requirements.txt
├── .dockerignore / .gitignore / .env.example
├── alembic.ini
├── alembic/versions/         # migrations de schéma versionnées
├── scripts/init_db.sql       # DDL exécuté au 1er démarrage du conteneur PostgreSQL
├── data/{raw,processed}/     # CSV locaux (ignorés par git)
├── tests/                    # pytest — 220 tests, SQLite en mémoire
└── app/
    ├── main.py                # point d'entrée FastAPI, montage des routers
    ├── core/
    │   ├── config.py            # Settings (variables d'environnement)
    │   ├── database.py          # engine SQLAlchemy, session, Base déclarative
    │   ├── security.py          # hachage bcrypt + JWT
    │   ├── deps.py               # dépendance FastAPI get_current_user
    │   └── preprocessing.py      # winsorizing partagé Maslaw/Abdoulmadjid
    ├── models/                 # ORM SQLAlchemy — 1 fichier par table
    │   ├── user.py · client.py · transaction.py · stock_produit.py · tresorerie.py
    ├── schemas/                 # contrats Pydantic entrée/sortie API
    │   ├── auth.py · client.py · analytics.py · copilot.py
    │   ├── ingestion.py · rfm_pipeline.py · report.py
    ├── services/                # logique métier, indépendante du HTTP
    │   ├── analytics_service.py       # Maslaw : KPIs, alertes stock
    │   ├── forecasting_service.py     # Maslaw : prévision Prophet/ARIMA/XGBoost
    │   ├── marketing_service.py       # Abdoulmadjid : segments RFM, actions marketing
    │   ├── rfm_clustering_service.py  # Abdoulmadjid : RFM + K-Means/DBSCAN/GMM
    │   ├── copilot_service.py         # assistant conversationnel mutualisé
    │   ├── llm_client.py               # client LLM multi-fournisseur (Phase 5)
    │   ├── ingestion_service.py        # orchestration de l'import universel
    │   └── report_service.py           # rapport PDF (WeasyPrint + Matplotlib)
    ├── routers/                  # endpoints HTTP
    │   ├── auth.py · analytics.py · marketing.py · copilot.py
    │   ├── ingestion.py · report.py
    └── ingestion/                 # scripts batch + pipeline générique
        ├── generic_loader.py · generic_transactions.py
        ├── generic_stock.py · generic_tresorerie.py
        ├── load_online_retail.py · simulate_tresorerie_stocks.py

frontend/
├── Dockerfile                 # build multi-étapes : Node (build) -> Nginx
├── nginx.conf                  # sert le build statique + proxy /api -> service "api"
├── docker-entrypoint.sh        # substitue la config runtime au démarrage
├── vite.config.js               # plugin Tailwind, code-splitting, proxy /api en dev
├── public/{config.js,favicon.svg}
└── src/
    ├── main.jsx · App.jsx · index.css
    ├── api/client.js             # point d'accès HTTP unique (+ jeton JWT)
    ├── hooks/{useApiHealth,useDatasetStatus}.js
    ├── lib/format.js              # formatage des nombres (cartes KPI)
    ├── components/{icons.jsx, layout/, ui/}
    ├── pages/
    │   ├── Connexion.jsx           # connexion / inscription
    │   ├── Accueil.jsx · Donnees.jsx
    │   ├── Segmentation.jsx        # module Abdoulmadjid
    │   ├── VentesStocks.jsx        # module Maslaw
    │   ├── Copilote.jsx · Rapport.jsx · IntrouvablePage.jsx
    └── test/                       # Vitest + Testing Library — 13 tests
```

---

## 4. Schéma PostgreSQL

Cinq tables (DDL complet dans `scripts/init_db.sql`), conformes au contrat d'interface du
Cadrage (§4) :

| Table | Rôle | Écrite par | Lue par |
|---|---|---|---|
| `users` | Comptes utilisateurs (authentification) | `auth` router | Dépendance `get_current_user` |
| `clients` | Identité client + colonnes RFM (`segment_rfm`, `score_churn`, `cluster_id`) | Abdoulmadjid | Maslaw, frontend, copilote |
| `transactions` | Historique des ventes, partagé | Ingestion commune | Abdoulmadjid (R/F/M), Maslaw (CA/BFR) |
| `stocks_produits` | Niveaux de stock, seuils d'alerte | Maslaw | Frontend, alertes |
| `tresorerie` | Mouvements d'encaissement/décaissement | Maslaw | Frontend, alertes BFR |

Une vue `v_kpis_globaux` agrège 4 des tables pour l'endpoint `/api/v1/analytics/kpis`.

---

## 5. Endpoints exposés

**Authentification** : toutes les routes ci-dessous, hors `/health` (racine, hors `/api/v1`),
`/api/v1/auth/register` et `/api/v1/auth/login`, exigent un jeton JWT
(`Authorization: Bearer <token>`) — cf. §6.

| Endpoint | Méthode | Sous-projet | Description |
|---|---|---|---|
| `/api/v1/auth/register` | POST | Mutualisé | Création de compte (email + mot de passe ≥ 8 caractères) — publique |
| `/api/v1/auth/login` | POST | Mutualisé | Connexion (form-urlencoded) → jeton JWT — publique |
| `/api/v1/auth/me` | GET | Mutualisé | Profil de l'utilisateur authentifié |
| `/api/v1/analytics/kpis` | GET | Maslaw | KPIs globaux (CA, trésorerie, stock, nb clients) |
| `/api/v1/analytics/forecast/sales` | GET | Maslaw | Prévision réelle (Prophet, ARIMA ou XGBoost, param `modele`), horizon 1–90 jours |
| `/api/v1/analytics/stock/alertes` | GET | Maslaw | Produits sous seuil d'alerte/réapprovisionnement |
| `/api/v1/marketing/clients` | GET | Abdoulmadjid | Liste des clients avec segmentation RFM |
| `/api/v1/marketing/segments/summary` | GET | Abdoulmadjid | Répartition par segment + action marketing recommandée |
| `/api/v1/marketing/rfm/run` | POST | Abdoulmadjid | Calcul RFM + clustering (K-Means, DBSCAN ou GMM, param `algorithme`), écrit en base |
| `/api/v1/copilot/chat` | POST | Mutualisé | Assistant conversationnel — LLM réel si configuré, sinon synthèse factuelle |
| `/api/v1/copilot/fournisseur` | GET | Mutualisé | Fournisseur et modèle LLM retenus d'après le `.env` (jamais la clé), avec avertissements de configuration |
| `/api/v1/ingestion/preview` | POST | Mutualisé | Upload fichier + suggestion de mapping de colonnes |
| `/api/v1/ingestion/import/transactions` | POST | Mutualisé | Import mappé → `clients` + `transactions` |
| `/api/v1/ingestion/import/stocks` | POST | Maslaw | Import mappé → `stocks_produits` |
| `/api/v1/ingestion/import/tresorerie` | POST | Maslaw | Import mappé → `tresorerie` |
| `/api/v1/report/pdf` | POST | Mutualisé | Génère le rapport PDF : résumé exécutif, KPIs, segmentation (3 algorithmes comparés), segments, clients, prévisions des ventes (3 modèles), trésorerie, stocks — avec interprétations (cf. §6, « Rapport PDF ») |

Documentation interactive générée automatiquement par FastAPI : `/docs` (Swagger, bouton
"Authorize" pour tester les routes protégées) et `/redoc`.

---

## 6. Choix des modèles & benchmarks

### Prévision des ventes (module Maslaw) — ARIMA vs Prophet vs XGBoost

La littérature converge sur un arbitrage précision/simplicité plutôt qu'un vainqueur universel :
ARIMA reste solide sur des séries courtes et stables ; Prophet est rapide à mettre en œuvre et
robuste aux ruptures/saisonnalités multiples ; XGBoost prend l'avantage dès que des variables
exogènes riches sont exploitables (MAE ~22,7 contre ~26,9 pour un ARIMAX et ~37,7 pour Prophet
dans un scénario avec variables externes). Les approches d'ensemble (moyenne pondérée des trois)
gagnent typiquement 5-10 % de précision par rapport au meilleur modèle individuel.

**Implémentation** (`GET /api/v1/analytics/forecast/sales?modele=prophet|arima|xgboost`) :
Prophet par défaut, ARIMA(1,1,1) et XGBoost en alternative, entraînés réellement sur le CA
journalier (`app/services/forecasting_service.py`). La série est prétraitée (winsorizing, cf.
plus bas) avant ajustement. RMSE/MAE calculés sur un découpage temporel train/test avant
ré-entraînement final sur tout l'historique. En dessous de 7 jours d'historique (14 pour
XGBoost, qui a besoin du lag J-7), l'API bascule sur une baseline naïve explicitement nommée
comme telle plutôt que de présenter un modèle non validé.

**XGBoost — variables exogènes** : dérivées de la date elle-même (pas d'un champ "promotions"
absent des exports PME réels) — jour de semaine, mois, week-end, jour férié camerounais (fêtes
fixes et mobiles via le package `holidays`), complétées par des variables autorégressives (lag
J-1, lag J-7, moyenne mobile 7 jours). Prévision multi-jours par récursion (chaque jour prédit
alimente les lags du suivant) : pas d'intervalle de confiance natif, avertissement explicite
au-delà d'un horizon de 14 jours.

**Mesure réelle sur AdventureWorks** (CA journalier de 1 127 jours, 29/12/2010 → 28/01/2014,
29 jours plafonnés ; validation sur les 20 % de jours les plus récents ; mesures du 12/10/2026,
reproductibles avec `python -m app.ingestion.export_adventureworks` puis l'import des fichiers) :

| Modèle | Ventes RMSE | Ventes MAE | Trésorerie RMSE | Trésorerie MAE |
|---|---:|---:|---:|---:|
| Prophet (défaut) | 28 546 | 17 786 | 1 253 557 | 1 112 103 |
| ARIMA(1,1,1) | 22 207 | 15 472 | **852 840** | **727 668** |
| XGBoost | **13 820** | **10 965** | 856 933 | 734 740 |

Sur ces données, **XGBoost divise par deux l'erreur de Prophet** sur les ventes (les variables de
calendrier et les retards J-1/J-7 captent la dynamique quotidienne) ; pour la trésorerie, ARIMA et
XGBoost sont à égalité et nettement devant Prophet. Prophet tourne ici **sans saisonnalité
annuelle** (désactivée dans `_previsions_prophet`), alors que la série couvre trois ans.

À l'inverse, le benchmark équitable de l'audit du 09/10/2026 (10 séries simulées de 90 jours, même
fenêtre de test) classait Prophet premier (RMSE 8,12 contre 16,89 pour ARIMA et 15,03 pour XGBoost
récursif). **Le meilleur modèle dépend donc des données.** Les anciens chiffres de ce README
(ARIMA 61,20 / XGBoost 30,69) ne sont plus reproductibles avec le code actuel et ont été retirés.

**Les 3 modèles sont comparés automatiquement (ajout du 13/10/2026)** : endpoint
`GET /api/v1/analytics/forecast/sales/comparaison?horizon_jours=30`, module
`app/services/comparaison_previsions.py`.
- **Calcul.** Les 3 modèles sont entraînés en parallèle sur la même série, avec le même protocole
  de validation. Un modèle en échec n'empêche pas l'affichage des deux autres.
- **Cache.** Le résultat est gardé en mémoire tant que les ventes ne changent pas. La clé du cache
  est la signature des données : nombre de ventes, dernière date, CA total.
- **Arrière-plan.** Le calcul est relancé automatiquement après chaque import de ventes
  (`PREVISIONS_PRECALCUL`). Deux demandes simultanées attendent le même calcul.
- **Mesures sur PostgreSQL avec AdventureWorks.** 3,2 à 4,2 s pour le premier calcul, puis
  instantané depuis le cache.
- **Interprétation déterministe** (sans LLM, donc reproductible), pour chaque modèle :
  - CA prévu, comparé aux 30 derniers jours réels ;
  - tendance (hausse, baisse ou stable à ±5 %) entre la première et la dernière semaine prévues ;
  - fiabilité : RMSE rapporté au CA moyen de la période de validation (bonne < 25 %,
    moyenne < 50 %, faible au-delà) ;
  - largeur de l'intervalle de confiance.
- **Synthèse.**
  - Modèle le plus fiable : celui dont le RMSE de validation est le plus bas.
  - Concordance ou divergence des modèles : alerte au-delà de 20 % d'écart sur le total prévu.
  - Rupture récente : signalée quand les 30 derniers jours s'écartent de plus de 50 % des
    90 jours précédents. Sur AdventureWorks, janvier 2014 ne fait que 46 k de CA contre
    1,5-1,9 M les mois précédents, ce qui explique l'écart entre Prophet et les deux autres.
- **Interface.** La page Ventes & Stocks n'a plus de choix de modèle ni de bouton.
  - À l'ouverture, elle affiche la synthèse, un graphique comparatif (CA réel des 90 derniers
    jours, 3 prévisions et intervalles de confiance), puis un graphique interprété par modèle.
  - L'horizon se règle sur 14, 30, 60 ou 90 jours.
  - L'endpoint `/forecast/sales?modele=…` (un seul modèle) reste disponible.

### Segmentation client (module Abdoulmadjid) — K-Means vs DBSCAN vs GMM

Score de silhouette comme mesure principale (K-Means ~0,55 sur données RFM structurées, DBSCAN
~0,68 quand la densité intra-segment est hétérogène), complété par l'indice de Davies-Bouldin et
le score de Calinski-Harabasz.

**Implémentation** (`POST /api/v1/marketing/rfm/run`, param `algorithme` : `auto` par défaut depuis
le 13/10/2026, ou `kmeans`, `dbscan`, `gmm` pour imposer un algorithme) : RFM sur fenêtre glissante ancrée sur la dernière transaction en base (pas la
date système), prétraitement (winsorizing puis log1p sur F et M) avant clustering, diagnostic
coude/silhouette, étiquetage **client par client** (cf. docstring `_assign_segments`) :

| Segment | Règle |
|---|---|
| À risque | ≥ 2 achats ET récence > moitié de la fenêtre (règle individuelle, absolue) |
| Perdu | 1 seul achat ET récence > moitié de la fenêtre, OU aucun achat dans la fenêtre |
| Nouveau | 1 seul achat récent |
| Fidèle | actif, récurrent, dans un cluster « haute valeur » ET scores F et M individuels ≥ 3 |
| Occasionnel | autres clients actifs récurrents |
| Atypique (B2B/grossiste) | DBSCAN uniquement : point isolé, actif, score M ≥ 4 |

`score_churn` = récence / fenêtre, borné à [0, 1] (heuristique absolue, cohérente avec les
segments : churn > 0,5 ⇔ dormant). *Corrections de l'audit du 2026-10-09 : l'ancienne version
étiquetait par récence MOYENNE du cluster (aucun « À risque » détecté sur données réalistes),
départageait les ex aequo par ordre de lecture en base, calculait le churn comme un rang relatif
et laissait les clients sortis de la fenêtre avec leur ancien segment — chaque bug est verrouillé
par un test de non-régression dans `tests/test_rfm_clustering_service.py`.*

**Reproductibilité** (corrigé le 12/10/2026) : K-Means et GMM dépendent de l'ordre des lignes, qui
était celui, non garanti, renvoyé par PostgreSQL ; les mêmes données donnaient k=2 ou k=6 d'un
import à l'autre. Les clients sont désormais triés sur leurs valeurs R/F/M avant le clustering
(`test_segmentation_reproductible_quel_que_soit_l_ordre_des_lignes`).

- **DBSCAN** n'a pas de *k* à choisir : epsilon est auto-sélectionné par détection de coude sur
  la courbe k-distance. Une minorité de clients trop petite pour former son propre cluster dense
  (< `min_samples`) est classée "bruit". Un point de bruit n'est étiqueté
  `Atypique (B2B/grossiste)` que s'il est actif et à forte valeur (score M ≥ 4) : vérifié sur
  données réalistes, le bruit DBSCAN contient surtout des clients dormants, qui suivent donc la
  règle À risque / Perdu. *Bug corrigé lors d'un audit : le calcul de la
  courbe k-distance appelait `NearestNeighbors.kneighbors(X)` avec le jeu de données passé
  explicitement, ce qui fait que chaque point se comptait comme son propre plus proche voisin
  (distance nulle) — scikit-learn n'exclut un point de ses propres voisins que lorsque `X` est
  omis. Corrigé (`.kneighbors()` sans argument), verrouillé par
  `test_dbscan_kdistance_excludes_each_point_from_its_own_neighbors`.*
- **GMM** : alternative "molle" à K-Means, même sélection par silhouette maximale, BIC reporté à
  titre indicatif.

**Mesure sur AdventureWorks** (17 614 clients actifs sur 365 jours, date de référence 28/01/2014,
code du 12/10/2026) :

| Algorithme | Paramètre choisi | Silhouette | Davies-Bouldin | Calinski-Harabasz |
|---|---:|---:|---:|---:|
| K-Means | k=6 | **0,488** | 0,762 | **17 075** |
| GMM | k=2 | 0,452 | 0,974 | 8 755 |
| DBSCAN | ε=0,116 (15 clusters + 181 bruit) | 0,226 | 0,942 | 2 306 |

Segments obtenus (K-Means, 18 484 clients) : Perdu 7 903, Nouveau 7 804, Occasionnel 1 433,
Fidèle 763, À risque 581. 84 % des clients n'ont qu'une facture : DBSCAN fragmente la densité et
n'est pas adapté comme segmentation principale sur ce jeu. Pour K-Means, k=5 a une silhouette
proche (0,476) et un meilleur Davies-Bouldin (0,675) : le choix par silhouette seule reste un
point de discussion méthodologique.

**Mesure de contrôle** (3 clients B2B à forte valeur + 80 clients réguliers, code du 12/10/2026) :

| Algorithme | Paramètre choisi | Silhouette | Davies-Bouldin | Calinski-Harabasz | B2B isolés en "Atypique" |
|---|---:|---:|---:|---:|---:|
| K-Means | k=2 | 0,724 | 0,250 | 53,7 | 0/3 (fusionnés en "Fidèle") |
| GMM | k=2 | 0,724 | 0,250 | 53,7 | 0/3 (fusionnés en "Fidèle") |
| DBSCAN | ε=0,526 | 0,511 | 0,491 | 38,0 | **3/3** |

Davies-Bouldin et Calinski-Harabasz étaient annoncés dans une version antérieure de cette section
sans être réellement calculés dans le code (`rfm_clustering_service.py` n'importait que
`silhouette_score`) — écart corrigé : les trois métriques sont désormais calculées ensemble pour
K-Means, GMM et DBSCAN (`_metriques_clustering`, exposées via `DiagnosticPoint`/l'API
`POST /marketing/rfm/run`). Sur ce scénario, les trois métriques racontent la MÊME histoire :
K-Means/GMM ont la silhouette la plus haute ET le meilleur (plus bas) Davies-Bouldin — cohérent
avec une partition mathématiquement plus compacte — mais c'est précisément le signal métier faux
positif documenté plus haut (fusion B2B/clients réguliers). DBSCAN, moins bon sur les trois
métriques prises isolément, reste le choix pertinent ici car aucune métrique de clustering interne
ne peut, par construction, "récompenser" l'isolement d'une minorité — cf. limites méthodologiques,
Cadrage Tâche #10.

K-Means et GMM convergent vers la solution à silhouette la plus haute, qui fusionne les B2B avec
une partie des clients réguliers — DBSCAN a une silhouette globale plus basse mais isole
correctement les 3 comptes B2B, ce que les deux autres ne peuvent pas faire. Le bon algorithme
dépend de l'objectif métier : K-Means/GMM pour une partition équilibrée exploitable en
masse-marketing, DBSCAN pour repérer les comptes à traiter hors campagnes grand public.

#### Évaluation automatique des 3 algorithmes (ajout du 13/10/2026)

En mode `auto` (défaut du bouton « Lancer le pipeline RFM »), K-Means, GMM et DBSCAN sont évalués
**en parallèle sur la même matrice RFM prétraitée**. Le meilleur est retenu et ses segments sont
enregistrés (`app/services/comparaison_segmentation.py`).

1. **Éligibilité métier.**
   - 2 à 8 groupes : au-delà, impossible de concevoir une action par groupe.
   - Au plus 10 % de clients non classés (bruit DBSCAN).
   - Silhouette calculable.
2. **Classement des algorithmes éligibles** par rang moyen sur silhouette (↑), Davies-Bouldin (↓)
   et Calinski-Harabasz (↑), départagé par la silhouette. Aucune métrique ne suffit seule : la
   silhouette favorise les groupes sphériques, Calinski-Harabasz les grands k, Davies-Bouldin les
   groupes compacts.
3. **Interprétation déterministe** (règles chiffrées, sans LLM), pour chaque algorithme :
   - choix de k ou d'epsilon, avec la courbe de silhouette ;
   - qualité selon l'échelle de Kaufman & Rousseeuw (1990) : > 0,70 forte, 0,51–0,70
     raisonnable, 0,26–0,50 faible, ≤ 0,25 non substantielle ;
   - séparation des groupes (Davies-Bouldin < 1), taille du plus petit groupe ;
   - segments obtenus ;
   - pour GMM, comparaison avec le minimum du BIC.
4. **Synthèse.**
   - Algorithme recommandé, classement, algorithmes écartés et pourquoi.
   - Part des clients qui reçoivent la même étiquette quel que soit l'algorithme : 94 % sur
     AdventureWorks. Les règles RFM individuelles pèsent plus que l'algorithme, qui départage
     surtout Fidèles et Occasionnels.

**Mesuré sur AdventureWorks (17 614 clients, PostgreSQL)**

| Algorithme | Paramètre | Silhouette | Davies-Bouldin | Calinski-Harabasz | Décision |
|---|---|---|---|---|---|
| K-Means | k = 6 | 0,488 | 0,76 | 17 075 | **Recommandé** |
| GMM | k = 2 | 0,452 | 0,97 | 8 800 | 2ᵉ |
| DBSCAN | ε = 0,116 | 0,224 | 0,94 | 2 300 | Écarté : 15 groupes |

- **Durée totale** : 40 à 45 s, contre 100 s sans les deux optimisations ci-dessous.
- **Parallélisme** : les 3 algorithmes tournent en même temps.
- **Silhouette échantillonnée** : au-delà de 10 000 clients, elle est estimée sur un échantillon
  fixe de 10 000 (`random_state=42`, option de scikit-learn). L'écart mesuré est d'environ 0,001,
  le résultat est reproductible, et Davies-Bouldin et Calinski-Harabasz restent calculés sur tous
  les clients.
- **Délai d'attente** : porté à 600 s (Nginx et interface) pour les machines lentes.
- **Algorithme imposé** : le menu « Algorithme » (Paramètres) permet toujours d'en imposer un, par
  exemple DBSCAN pour isoler des comptes B2B, au prix d'une segmentation moins exploitable en masse.

### Rapport PDF — toutes les analyses, résumées et interprétées (refonte du 13/10/2026)

`POST /api/v1/report/pdf` (`app/services/report_service.py`) assemble 9 sections, dans cet ordre :

1. Page de garde.
2. **Résumé exécutif** : une conclusion par module (activité, concentration des clients,
   segmentation, segments, ventes, point de vigilance, trésorerie, stocks).
3. Indicateurs clés.
4. **Segmentation** :
   - comparaison des 3 algorithmes (tableau des métriques, décision, méthode de choix) ;
   - graphique des silhouettes selon k ou ε ;
   - interprétation détaillée de chaque algorithme.
5. **Segments et actions recommandées** : graphique, tableau (part, montant moyen, churn, action) et
   lecture (segment le plus nombreux, le plus rentable, le plus à risque).
6. **Clients** : concentration du CA (Pareto 80 %) et meilleurs clients.
7. **Prévision des ventes** : synthèse, graphique (réel et 3 modèles avec intervalles), tableau
   (CA prévu, RMSE, MAE, tendance, fiabilité), méthode de validation, interprétation par modèle.
8. **Trésorerie** : solde réel et prévision, niveau d'alerte, point le plus bas, modèle.
9. **Stocks** : décompte des alertes et les 15 plus urgentes.

Choix de conception :
- **Aucun recalcul.** Le rapport lit la dernière segmentation enregistrée (la table
  `historique_segmentation` garde désormais la comparaison et la synthèse) et les prévisions mises
  en cache. Ses chiffres et interprétations sont donc **identiques à l'écran**.
- **Rapidité.** Génération en environ 7 s sur AdventureWorks, contre 20 s avant (le rapport
  relançait une segmentation K-Means).
- **Robustesse.** Une section en échec affiche « indisponible » sans bloquer le reste du rapport.
- **Compatibilité.** Les anciens identifiants de section (`coude_silhouette`, `top_clients`…)
  restent acceptés.
- **Avant de générer le rapport**, lancer la segmentation en mode automatique ; sinon la section
  Segmentation explique quoi faire.

### Prétraitement anti-valeurs-extrêmes (winsorizing)

Un dataset PME uploadé doit être prétraité statistiquement avant d'alimenter les modèles, pas
seulement nettoyé à l'ingestion : une seule valeur aberrante (faute de frappe sur un prix)
suffit à distordre un modèle sensible à l'échelle (RMSE de prévision passant de ~7 à ~13 380 sur
un cas de test sans correctif).

**Implémentation** (`app/core/preprocessing.py::winsoriser`) : plafonne une série SANS supprimer
aucune ligne, en combinant un plafond au 99ᵉ percentile et la clôture de Tukey (Q3 + 1,5×IQR), et
retient le plus protecteur des deux — un percentile seul est quasi inopérant sur un petit jeu de
données PME. **Seule la valeur utilisée pour le calcul du modèle est plafonnée** : les agrégats
réels stockés en base (montants affichés dans les KPIs/le rapport PDF) ne sont jamais modifiés.

### Authentification JWT

Périmètre volontairement **"simple"** (décision produit) : un seul rôle implicite — tout
utilisateur authentifié a accès à l'intégralité de l'API — plutôt qu'un RBAC complet par module,
disproportionné pour un outil interne à une équipe de deux-trois personnes.

**Implémentation** (`app/core/security.py`, `app/core/deps.py`, `app/routers/auth.py`) :
- Mots de passe hachés avec `bcrypt` directement (`passlib[bcrypt]` s'est révélé incompatible
  avec les versions récentes de `bcrypt`, testé avant d'être écarté).
- Jetons JWT (`python-jose`), expiration configurable (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- Chaque router métier protégé en une ligne (`dependencies=[Depends(get_current_user)]`).
  `/health` reste public (indicateur "API connectée" du frontend).
- `POST /auth/login` compatible `OAuth2PasswordRequestForm` : testable directement depuis le
  bouton "Authorize" de Swagger `/docs`.
- `POST /auth/register` volontairement ouverte (pas de clé d'invitation) — à restreindre avant
  tout déploiement public.

**Frontend** : jeton stocké en `localStorage`, attaché automatiquement à chaque requête
(intercepteur Axios). Un 401 en cours de session purge le jeton et renvoie vers `/connexion`.

### Copilote — LLM multi-fournisseur

Pas de ChromaDB/index vectoriel : le contexte est injecté directement dans le prompt système via
`copilot_service.build_context_snapshot`, qui vise l'EXHAUSTIVITÉ des analyses déjà calculées
ailleurs dans l'application plutôt qu'un résumé partiel — KPIs globaux, alertes stock détaillées,
segmentation RFM complète par segment (montant moyen, churn moyen, action recommandée, date de
dernière mise à jour) avec un échantillon nommé de clients par segment, prévision des ventes
(modèle, RMSE/MAE, premier/dernier point) et prévision de trésorerie + niveau d'alerte. Le
détail ligne à ligne (des dizaines de milliers de transactions) n'est pas injecté : le copilote
le consulte à la demande par une requête SQL en lecture seule (voir « Accès à la base » ci-dessous). Les deux prévisions du contexte utilisent ARIMA plutôt
que Prophet (défaut de la plateforme sur `GET /analytics/forecast/sales`) pour que le chat reste
réactif — l'ajustement CmdStan/Prophet ajoute plusieurs secondes par appel, deux fois, à chaque
message envoyé au copilote.

Un appel HTTP direct via `httpx` (`app/services/llm_client.py`) suffit pour chacun des 5
fournisseurs supportés (Anthropic, OpenAI, Groq, Google, OpenRouter), pas besoin de SDK dédié.
Le premier fournisseur dont la clé est configurée est utilisé automatiquement. Tout échec d'appel
(réseau, authentification, quota) bascule proprement sur la synthèse factuelle locale — jamais de
500 brut.

**Ajouts du 12/10/2026** : `LLM_BASE_URL` (`.env`) remplace l'URL officielle du fournisseur (LLM
local Ollama/LM Studio, proxy, ou serveur simulé du test de bout en bout) ; chaque réponse de
`POST /copilot/chat` indique son origine (`mode` : `llm`, `local` ou `secours`, avec `fournisseur`
et `modele`), affichée sous la réponse dans l'interface ; le contexte n'injecte plus que le
décompte des alertes de stock et les 25 plus urgentes (au lieu de la liste complète : 15 000 →
5 000 caractères sur AdventureWorks) ; une question vide ou de plus de 2 000 caractères est refusée
(422). Vérifié de bout en bout avec un LLM simulé : réponse normale, erreur 500, clé refusée,
réponse vide, réponse illisible et dépassement du délai de 20 s basculent tous sur la synthèse de
secours, sans erreur côté utilisateur.

#### Changer de fournisseur en changeant seulement la clé (14/10/2026)

Il suffit de coller la nouvelle clé dans le `.env` puis de redémarrer l'API ; aucun autre
réglage n'est nécessaire.

| Clé collée | Fournisseur reconnu | Modèle par défaut |
|---|---|---|
| `sk-ant-…` | Anthropic | `claude-haiku-5-5` |
| `sk-proj-…` / `sk-…` | OpenAI | `gpt-4o-mini` |
| `gsk_…` | Groq | `openai/gpt-oss-20b` |
| `AQ.…` / `AIza…` | Google Gemini | `gemini-3.6-flash` |
| `sk-or-…` | OpenRouter | `openrouter/free` |

- Le fournisseur est reconnu au **préfixe de la clé**. La clé peut donc être collée dans sa
  variable (`GOOGLE_API_KEY`…), dans la variable générique `LLM_API_KEY`, ou même dans la
  mauvaise variable (une clé `gsk_` dans `GOOGLE_API_KEY` part bien chez Groq, avec un
  avertissement).
- `LLM_MODEL` n'est appliqué que s'il appartient au fournisseur retenu. Un ancien
  `LLM_MODEL=gemini-…` oublié après le passage à OpenAI est ignoré (avertissement) au lieu de
  provoquer une erreur 404.
- Espaces, retours chariot et guillemets autour des valeurs sont retirés.
- **Plusieurs clés renseignées** : la priorité anthropic → openai → groq → google → openrouter
  s'applique, sauf si `LLM_PROVIDER` en désigne une autre. Le plus simple : une seule clé.
- **Limites** : `LLM_BASE_URL` (Ollama, proxy) s'applique à tous les fournisseurs et doit être
  vidé pour revenir à l'API officielle ; une clé au format inconnu (serveur local) reste
  attribuée à la variable où elle est collée.
- **Vérification** : bandeau en haut de la page Copilote (« LLM utilisé : google ·
  gemini-3.6-flash (clé lue dans GOOGLE_API_KEY) » et avertissements éventuels),
  `GET /api/v1/copilot/fournisseur`, ou la ligne « Copilote : fournisseur LLM … » dans
  `docker compose logs api`.

#### Accès à la base (ajout du 13/10/2026)

Avec un LLM configuré, le copilote interroge directement la base pour répondre (texte → SQL,
`app/services/sql_agent.py`) :

1. Le LLM reçoit le schéma documenté des 4 tables métier (`clients`, `transactions`,
   `stocks_produits`, `tresorerie`) avec la question, et écrit une requête `SELECT`. Il répond
   `AUCUNE` si la question n'a pas besoin de la base (salutation, explication de méthode).
2. L'application analyse la requête (sqlglot), l'exécute en lecture seule et transmet au plus
   50 lignes au LLM (200 lues). En cas de refus ou d'erreur SQL, l'erreur est renvoyée au LLM, qui
   corrige une fois.
3. Le LLM rédige la réponse à partir des lignes réelles. La requête exécutée est renvoyée
   (`requete_sql`, `lignes_base`) et visible sous la réponse dans l'interface.

Choix du texte → SQL plutôt que des « outils » (function calling) : les 5 fournisseurs ont des
formats d'outils différents, alors qu'une complétion de texte marche partout, y compris avec un
LLM local. Une requête couvre aussi les questions imprévues (« CA par pays », « produits jamais
vendus »).

**Sécurité (chaque couche bloque seule une écriture) :**
- **Analyse syntaxique.** Les règles :
  - une seule instruction, de lecture (`SELECT` / `WITH` / `UNION`) ;
  - aucun nœud d'écriture ou de DDL (y compris `WITH … DELETE`, `SELECT … INTO`, `FOR UPDATE`) ;
  - tables en liste blanche (ni `users` ni schémas système) ;
  - colonnes `email` et `mot_de_passe_hache` interdites, `SELECT *` interdit ;
  - fonctions système interdites (`pg_sleep`, `pg_read_file`, `set_config`, `dblink`…).
- **Exécution.** Transaction `READ ONLY`, `statement_timeout` de 5 s, `ROLLBACK` final, sur une
  connexion séparée de celle de l'API.
- **Rôle dédié (optionnel).** `scripts/init_db.sql` crée le rôle PostgreSQL `copilote_lecture` :
  - il n'a que le droit `SELECT` ;
  - il n'a aucun accès à `users` ni à `clients.email` ;
  - la lecture seule et le délai de 5 s sont imposés par le rôle lui-même.

  Pour l'activer, ajouter dans `.env` :
  `COPILOTE_DATABASE_URL_LECTURE=postgresql+psycopg2://copilote_lecture:lecture_change_me@db:5432/copilote_pme`
- **Désactivation.** `COPILOTE_ACCES_BASE=false` désactive l'accès : le copilote ne répond plus
  qu'avec la synthèse d'indicateurs.

**Mesuré sur PostgreSQL 16 avec AdventureWorks** (LLM simulé qui écrit le SQL), 6 questions :
- top clients par CA, CA par pays, CA mensuel sur la dernière année de données, produits jamais
  vendus, top des décaissements, e-mails des clients fidèles ;
- 1,2 à 1,9 s par question hors temps du LLM ;
- la demande d'e-mails est refusée puis corrigée par le LLM ;
- résultats identiques via le rôle `copilote_lecture`.

Coût : 2 appels au LLM par question (3 si une correction est nécessaire), au lieu d'un seul.

#### Accès à toutes les données de l'application (ajout du 13/10/2026)

Le copilote voit maintenant tout ce que l'application stocke ou calcule :

| Donnée | Comment le copilote y accède |
|---|---|
| Clients, ventes, stocks, trésorerie | requêtes SQL en lecture seule (jusqu'à 3 par question) |
| Segmentation RFM de chaque client | SQL (`clients` : scores, segment, cluster, churn) |
| Métriques de chaque segmentation (k, silhouette, Davies-Bouldin, diagnostic par k) | nouvelle table `historique_segmentation` (SQL) et résumé de la dernière exécution dans le contexte |
| Prévisions des ventes des 3 modèles et leur interprétation | contexte : même calcul et même cache que la page Ventes & Stocks, avec un détail par semaine du modèle le plus fiable |
| Prévision de trésorerie et niveau d'alerte | contexte |
| KPIs, alertes de stock, top clients, synthèse des segments | contexte |
| E-mails des clients | SQL, seulement si `COPILOTE_ACCES_EMAILS=true` (désactivé par défaut) |
| Comptes utilisateurs et mots de passe | jamais |

- **Plusieurs requêtes.** Une question peut croiser plusieurs sources : par exemple, « le produit le
  plus vendu et son stock » donne 2 requêtes. Si une requête échoue, seule celle-ci est renvoyée au
  LLM pour correction ; les autres sont conservées.
- **Nouvelle table `historique_segmentation`.**
  - Une ligne par segmentation écrite en base (les simulations `dry_run` ne sont pas enregistrées).
  - Créée par `scripts/init_db.sql`, par la migration Alembic `b7d3e9a41c20`, et automatiquement
    au démarrage de l'API si elle manque : une base existante l'obtient sans être réinitialisée.
  - Le rôle `copilote_lecture` reçoit le droit de la lire au démarrage.
- **E-mails désactivés par défaut.** La question et les lignes lues partent chez le fournisseur LLM
  externe : un e-mail client est une donnée personnelle.
- **Vérifié sur PostgreSQL avec AdventureWorks, via le rôle `copilote_lecture`.**
  - La table est créée au démarrage sur une base qui ne l'avait pas.
  - Le contexte contient les 3 prévisions et la dernière segmentation (k = 6, silhouette 0,488).
  - 3 questions croisées répondues en 0,7 à 0,9 s hors temps du LLM. Exemple : le produit le plus
    vendu, Water Bottle (4 244 unités), n'a plus qu'une unité en stock pour un seuil de 3.

---

## 7. Installation

Deux méthodes équivalentes, à ne pas mélanger pour la base de données (chacune a son propre
schéma appliqué séparément).


### 7.a. Option A — Docker (recommandé)

Lance PostgreSQL, l'API et le frontend ensemble, schéma déjà appliqué, rien à installer sur la
machine à part Docker.

**Prérequis**
- Docker Desktop (Windows/Mac) ou Docker Engine + plugin Compose (Linux)
- `docker --version` et `docker compose version` fonctionnent
- BuildKit actif (par défaut avec `docker compose` v2 ; avec l'ancien `docker-compose` v1,
  exporter `DOCKER_BUILDKIT=1` et `COMPOSE_DOCKER_CLI_BUILD=1`)

**Étapes**

```bash
# 1. Configuration
cd copilote-ia-pme
cp .env.example .env
# éditer .env : changer au minimum POSTGRES_PASSWORD et SECRET_KEY

# 2. Construction + démarrage (API + Frontend + PostgreSQL)
docker compose up --build
# ou en arrière-plan :
docker compose up --build -d

# 3. Vérifier
docker compose ps
curl http://localhost:8000/health   # {"status":"ok"}
```

Ouvrir **http://localhost:3000** (frontend ; Nginx écoute sur 8501 dans le conteneur, publié
sur 3000 par `docker-compose.yml`) — créer un compte au premier accès (onglet
« Créer un compte », email + mot de passe ≥ 8 caractères).

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger (tester l'API, bouton "Authorize") | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

**Commandes utiles**

```bash
docker compose logs -f api        # logs en direct d'un service (api / frontend / db)
docker compose up --build frontend # reconstruire uniquement le frontend après un changement
docker compose down               # arrêt, conserve les données PostgreSQL
docker compose down -v            # arrêt + reset complet de la base
```

**Dépannage rapide**

| Symptôme | Cause probable | Solution |
|---|---|---|
| `api` redémarre en boucle | L'API a démarré avant PostgreSQL | Normal au premier lancement (healthcheck) ; sinon `docker compose logs db` |
| Port déjà utilisé | Un autre service occupe le port | Modifier le mapping dans `docker-compose.yml` |
| Changements de code non pris en compte | Image non reconstruite | `docker compose up --build` |
| Inscription/connexion : « Connexion à la base de données refusée » (logs : `role "copilote_user" does not exist` ou `password authentication failed`) | Le volume PostgreSQL a été créé avec d'autres identifiants : `POSTGRES_USER`/`POSTGRES_PASSWORD` ne sont lus qu'à la **première** création du volume | `docker compose down -v` puis `docker compose up --build` (efface la base : réimporter les fichiers), en gardant `POSTGRES_*` et `DATABASE_URL` cohérents dans `.env` |
| Copilote : « Le fournisseur LLM configuré (google) n'a pas répondu correctement (… 404 … is not found) » | Le modèle demandé a été retiré par le fournisseur (ex. `gemini-1.5-flash`) | Mettre à jour le code (défauts actuels depuis corrections9) **ou** indiquer un modèle actuel dans `.env` (`LLM_MODEL=gemini-3.6-flash`, par exemple), puis `docker compose up -d --build api` |
| Copilote : « 503 … This model is currently experiencing high demand » ou « momentanément surchargé » | Surcharge temporaire chez le fournisseur (côté Google, pas l'application) | Rien à faire : l'application réessaie 2 fois puis bascule sur `gemini-3.5-flash-lite`. Si tout reste surchargé, la synthèse de secours s'affiche ; reposer la question quelques instants plus tard |
| `scripts/init_db.sql` modifié mais tables inchangées | Le script ne s'exécute qu'au 1ᵉʳ démarrage du volume | `docker compose down -v` puis `up --build` |
| `POST /report/pdf` échoue en `OSError` | Image construite avant l'ajout des libs WeasyPrint | `docker compose up --build` |
| Frontend affiche du contenu périmé | Nginx sert un build statique figé | `docker compose up --build frontend` |

### 7.b. Option B — Installation locale (sans Docker)

Utile pour développer avec rechargement à chaud, ou sur une machine où Docker n'est pas disponible.
Procédure vérifiée de bout en bout le 13/10/2026 :
- Python 3.11, PostgreSQL 16 vierge, Node 22 ;
- import des 3 fichiers, segmentation, prévisions des 3 modèles, copilote et rapport PDF ;
- aucune erreur.

Deux terminaux restent ouverts pendant l'utilisation : un pour l'API, un pour l'interface.

#### Étape 0 — Prérequis

| Logiciel | Version | Windows | Linux / macOS |
|---|---|---|---|
| **Python** | **3.11 ou 3.12** (pas 3.13 : numpy 1.26 et pandas 2.2, versions figées, ne s'y installent pas) | [python.org](https://www.python.org/downloads/) — cocher « Add python.exe to PATH » | `sudo apt install python3.11 python3.11-venv` / `brew install python@3.11` |
| **PostgreSQL** | 14 ou plus | [installeur EDB](https://www.postgresql.org/download/windows/) — noter le mot de passe de `postgres` ; installe aussi `psql` et pgAdmin | `sudo apt install postgresql` / `brew install postgresql@16` |
| **Node.js** | **20.19+ ou 22.12+** (exigé par Vite 8) | [nodejs.org](https://nodejs.org/) (version LTS) | `nvm install 22` ou le gestionnaire de paquets |
| GTK3 / Pango *(optionnel)* | — | [GTK3 Runtime pour Windows](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases) | `sudo apt install libpango-1.0-0 libpangoft2-1.0-0` |

GTK/Pango ne sert qu'au **rapport PDF** (WeasyPrint). Sans GTK, toute l'application fonctionne et
seul le bouton « Générer le PDF » affiche la marche à suivre (erreur 503). Avant le correctif du
13/10/2026, l'absence de GTK empêchait l'API de démarrer.

Vérifier les versions :

```bash
python --version      # Windows : py -3.11 --version
psql --version
node --version
```

#### Étape 1 — Créer l'utilisateur et la base PostgreSQL

Ouvrir `psql` en tant que `postgres`. Sous Windows : menu Démarrer → « SQL Shell (psql) », Entrée
pour les valeurs par défaut, puis le mot de passe choisi à l'installation. Sous Linux :
`sudo -u postgres psql`. Puis :

```sql
CREATE USER copilote_user WITH PASSWORD 'change_me';
CREATE DATABASE copilote_pme OWNER copilote_user;
\q
```

Changer `change_me` si besoin, et reporter le même mot de passe à l'étape 3.

#### Étape 2 — Installer le backend (Python)

Dans le dossier du projet, celui qui contient `requirements.txt`.

**Windows (PowerShell)**

```powershell
cd D-veloppement-d-un-copilote-intelligent-pour-l-analyse-des-clients-main
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
# Si PowerShell refuse (« l'exécution de scripts est désactivée ») :
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   puis relancer la ligne ci-dessus
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps xgboost==2.1.1
```

**Linux / macOS**

```bash
cd D-veloppement-d-un-copilote-intelligent-pour-l-analyse-des-clients-main
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps xgboost==2.1.1
```

Deux remarques :
- **xgboost** n'est pas dans `requirements.txt`, volontairement : son paquet PyPI entraîne
  `nvidia-nccl-cu12` (~340 Mo, multi-GPU), inutile ici. `--no-deps` l'évite, et ses vraies
  dépendances (numpy, scipy) sont déjà installées.
- **Durée** : l'installation prend quelques minutes (Prophet, scikit-learn, statsmodels…).
  Le terminal affiche `(.venv)` quand l'environnement est actif.

#### Étape 3 — Configurer `.env`

```powershell
copy .env.example .env      # Windows
```
```bash
cp .env.example .env        # Linux / macOS
```

Ouvrir `.env` et modifier :

```ini
POSTGRES_HOST=localhost
DATABASE_URL=postgresql+psycopg2://copilote_user:change_me@localhost:5432/copilote_pme
SECRET_KEY=une_longue_phrase_secrete_a_changer
# Copilote avec LLM (facultatif) : une seule clé suffit, par exemple
GOOGLE_API_KEY=...
```

Dans `DATABASE_URL`, le mot de passe et le port (5432 par défaut) doivent correspondre à
l'étape 1. Sans clé LLM, le copilote fonctionne en mode synthèse locale.

**Clé Gemini (Google AI Studio).**
- Les nouvelles clés commencent par `AQ.` (les anciennes par `AIza`) : les deux fonctionnent.
  L'application envoie la clé dans l'en-tête officiel `x-goog-api-key`.
- Coller la clé telle quelle, sans guillemets ni espace : `GOOGLE_API_KEY=AQ.xxxx`.
- Si l'ancienne clé est encore dans `LLM_API_KEY`, `ANTHROPIC_API_KEY`, etc., la vider. Sinon,
  ajouter `LLM_PROVIDER=google` pour forcer ce fournisseur.
- Après tout changement de `.env`, redémarrer l'API. Avec Docker :
  `docker compose up -d --force-recreate api` (`docker compose restart api` ne relit **pas** le
  `.env`). Sans Docker : arrêter uvicorn (Ctrl+C) et le relancer.
- Le bandeau en haut de la page Copilote indique le fournisseur et le modèle retenus.
- Sous la réponse du copilote, la mention « Réponse rédigée par le LLM · google (gemini-3.6-flash) »
  confirme que la clé fonctionne.
- Ne jamais publier `.env` : il est exclu de Git par `.gitignore`.

#### Étape 4 — Créer les tables

```bash
alembic upgrade head
```

Résultat attendu : 3 lignes `Running upgrade …`, qui créent `clients`, `transactions`,
`stocks_produits`, `tresorerie`, `users` et `historique_segmentation`. Un avertissement
« Rôle copilote_lecture non créé » est normal avec un utilisateur non administrateur : ce rôle en
lecture seule du copilote est optionnel (cf. §6, « Accès à la base »).

#### Étape 5 — Lancer l'API (terminal 1)

```bash
uvicorn app.main:app --reload
```

Vérifier dans le navigateur :
- http://localhost:8000/health doit afficher `{"status":"ok"}` ;
- http://localhost:8000/docs donne la documentation interactive de l'API.

#### Étape 6 — Lancer l'interface (terminal 2)

```bash
cd frontend
npm install
npm run dev
```

Ouvrir **http://localhost:5173**. Le proxy de Vite (`frontend/vite.config.js`) redirige `/api` et
`/health` vers l'API sur le port 8000 : aucune configuration en plus, l'API doit juste tourner.

#### Étape 7 — Premier usage

1. Créer un compte (bouton « Créer un compte »).
2. Page **Données** : importer `ventes.csv`, puis `stock.csv`, puis `tresorerie.csv` (jeu
   AdventureWorks, cf. §1 ; ou les petites versions de test). Les menus se déverrouillent dès la
   fin du premier import.
3. **Segmentation RFM** → « Lancer le pipeline RFM ».
4. **Ventes & Stocks** : les prévisions des 3 modèles se calculent seules (quelques secondes la
   première fois).
5. **Copilote** et **Rapport**.

#### Les fois suivantes

Il suffit de relancer les deux serveurs, PostgreSQL tournant déjà comme service :

```bash
# terminal 1, dans le dossier du projet
.venv\Scripts\Activate.ps1        # Windows (Linux/macOS : source .venv/bin/activate)
uvicorn app.main:app --reload
# terminal 2
cd frontend && npm run dev
```

Après une mise à jour du code, relancer `pip install -r requirements.txt` et `alembic upgrade head`
(sans effet si rien n'a changé), puis `npm install` dans `frontend/`.

#### Dépannage (installation locale)

| Symptôme | Cause | Solution |
|---|---|---|
| `pip install` échoue sur numpy, pandas ou psycopg2 (« Failed building wheel ») | Python 3.13 | Recréer le `.venv` avec Python 3.11 ou 3.12 (`py -3.11 -m venv .venv`) |
| `password authentication failed for user "copilote_user"` | Mot de passe différent entre l'étape 1 et `DATABASE_URL` | Corriger `.env`, ou `ALTER USER copilote_user WITH PASSWORD '…';` dans psql |
| `database "copilote_pme" does not exist` / `connection refused` | Étape 1 non faite, ou PostgreSQL arrêté | Refaire l'étape 1 ; Windows : Services → `postgresql-x64-16` → Démarrer |
| `relation "clients" does not exist` | Tables non créées | `alembic upgrade head` (étape 4), avec le `.venv` actif |
| `alembic` / `uvicorn` « n'est pas reconnu » | Environnement virtuel non activé | Activer le `.venv` (étape 2) |
| L'interface affiche « API injoignable » | L'API ne tourne pas sur le port 8000 | Lancer l'étape 5 dans un autre terminal |
| Port 5432 ou 8000 déjà utilisé | Autre PostgreSQL ou autre API | Adapter le port dans `DATABASE_URL`, ou `uvicorn app.main:app --reload --port 8001` et `target` dans `frontend/vite.config.js` |
| « Génération du PDF indisponible… GTK3 Runtime » | GTK/Pango absent | Installer GTK3 Runtime (Windows) ou libpango (Linux), puis redémarrer l'API |
| Erreur npm « Unsupported engine » ou Vite qui ne démarre pas | Node trop ancien | Installer Node 22 LTS |
| `'Prophet' object has no attribute 'stan_backend'` | `cmdstanpy` incompatible (corrigé : figé à 1.2.4) | `pip install -r requirements.txt` pour obtenir la bonne version |

**Raccourcis `make`** (Linux/macOS ; `make` n'existe pas par défaut sous Windows) :

```bash
make install         # pip install -r requirements.txt + xgboost (--no-deps)
make run             # uvicorn app.main:app --reload
make migrate         # alembic upgrade head
make frontend-install  # npm ci (frontend/)
make frontend        # npm run dev
make test            # pytest
```

---

## 8. Tests & qualité

```bash
make test              # pytest — 220 tests, SQLite en mémoire, aucune dépendance PostgreSQL
make lint              # ruff check app tests
make frontend-test     # Vitest — 23 tests (rendu jsdom, sans navigateur réel)
cd frontend && npx oxlint   # lint frontend
```

---

### Analyse de robustesse des prévisions (rapport, tableau 9)

`python -m scripts.analyse_robustesse_previsions` compare, sur les ventes importées, les trois
modèles à deux prévisions naïves (RMSE, MAE, MASE), sur la série complète puis tronquée
(`--coupure 2013-12-28`). Les erreurs de Prophet varient de moins de 1 % d'une exécution à l'autre.

### Test de bout en bout (navigateur + LLM simulé)

`scripts/e2e/` rejoue le parcours complet d'un utilisateur dans Chromium
(inscription, import des 3 fichiers, segmentation, ventes & stocks, copilote
avec panne du LLM simulée, rapport PDF) contre un faux fournisseur LLM
compatible OpenAI, sans clé ni Internet — cf. `scripts/e2e/README.md`. Le
réglage `LLM_BASE_URL` (`.env`) qui le rend possible sert aussi à brancher un
LLM local (Ollama, LM Studio) ou un proxy.

Dernière exécution (12/10/2026, PostgreSQL 16, Nginx avec `frontend/nginx.conf`, AdventureWorks) :
parcours complet en 135 s, aucune requête en échec ; import des ventes (60 398 lignes) en 44 s
dont segmentation automatique ; 9/9 contrôles de santé de l'API réussis pendant l'import.

## 9. État d'avancement

Toutes les phases identifiées dans la Roadmap sont couvertes :

- [x] Jeu de données identifié et documenté (§1)
- [x] Cahier des charges fonctionnel (Cahier des Charges Fonctionnel Détaillé, 25 slides)
- [x] Schéma de base de données (`scripts/init_db.sql` + migrations Alembic versionnées, 5 tables + vue KPIs)
- [x] Architecture 4 couches (Router → Schema → Service → ORM)
- [x] Import universel de données PME (mapping de colonnes, prétraitement)
- [x] Segmentation RFM + clustering réel — K-Means, **DBSCAN et GMM** (§6)
- [x] Prévision des ventes réelle — Prophet, ARIMA et **XGBoost** (§6)
- [x] Génération du rapport PDF
- [x] Copilote conversationnel — **LLM réel multi-fournisseur** (§6)
- [x] **Authentification JWT** — toute l'API protégée (§6)
- [x] Frontend unifié (React + Vite), servi en production par Nginx
- [x] Test d'intégration bout-en-bout (`tests/test_end_to_end_pipeline.py`) + suite frontend
- [x] Test de bout en bout navigateur + LLM simulé (`scripts/e2e/`)
- [x] Jeu complet ventes + stock + trésorerie prêt à importer (AdventureWorks, §1)

Pistes pour la Phase 5, hors périmètre actuel :
- RBAC complet (rôles/permissions différenciés par module) si le périmètre "simple" actuel s'avère insuffisant
- Ensemble pondéré ARIMA + Prophet + XGBoost pour la prévision (gain typique 5-10 %)
- Modèle de churn entraîné (BG/NBD ou régression logistique) en remplacement de l'heuristique récence / fenêtre
- Sélection automatique du modèle de prévision (plus faible RMSE de validation) et validation glissante
- Réimport idempotent (réimporter le même fichier double aujourd'hui le CA) et quantités décimales
