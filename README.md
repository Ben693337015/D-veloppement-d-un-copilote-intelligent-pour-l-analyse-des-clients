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
                        │  (port 3000)               │
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
reverse-proxy : le navigateur n'appelle qu'une seule origine (`localhost:3000`), Nginx redirige
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
├── tests/                    # pytest — 86 tests, SQLite en mémoire
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
| `/api/v1/ingestion/preview` | POST | Mutualisé | Upload fichier + suggestion de mapping de colonnes |
| `/api/v1/ingestion/import/transactions` | POST | Mutualisé | Import mappé → `clients` + `transactions` |
| `/api/v1/ingestion/import/stocks` | POST | Maslaw | Import mappé → `stocks_produits` |
| `/api/v1/ingestion/import/tresorerie` | POST | Maslaw | Import mappé → `tresorerie` |
| `/api/v1/report/pdf` | POST | Mutualisé | Génère le rapport PDF (page de garde, KPIs, coude/silhouette, segments, top clients) |

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

**Mesure réelle** (série simulée 90 jours, tendance + saisonnalité hebdomadaire + bruit, horizon
de validation 14 jours) :

| Modèle | RMSE (validation) | MAE (validation) |
|---|---:|---:|
| ARIMA(1,1,1) | 61,20 | 46,38 |
| XGBoost | 30,69 | 22,86 |

XGBoost divise le RMSE par ~2 par rapport à ARIMA sur cette série — les variables de calendrier
apportent un signal qu'ARIMA, univarié, ne peut pas exploiter. À reproduire sur les données PME
réelles avant de trancher définitivement pour le rapport final.

### Segmentation client (module Abdoulmadjid) — K-Means vs DBSCAN vs GMM

Score de silhouette comme mesure principale (K-Means ~0,55 sur données RFM structurées, DBSCAN
~0,68 quand la densité intra-segment est hétérogène), complété par l'indice de Davies-Bouldin et
le score de Calinski-Harabasz.

**Implémentation** (`POST /api/v1/marketing/rfm/run`, param `algorithme` : `kmeans` par défaut,
`dbscan` ou `gmm`) : RFM sur fenêtre glissante ancrée sur la dernière transaction en base (pas la
date système), prétraitement (winsorizing) avant clustering, diagnostic coude/silhouette,
étiquetage des segments par seuils **absolus** par axe R/F/M (pas un classement relatif entre
clusters — cf. docstring `_assign_segments`).

- **DBSCAN** n'a pas de *k* à choisir : epsilon est auto-sélectionné par détection de coude sur
  la courbe k-distance. Une minorité de clients trop petite pour former son propre cluster dense
  (< `min_samples`) est classée "bruit" — étiquetée `Atypique (B2B/grossiste)`, un segment que
  K-Means ne peut structurellement pas produire. *Bug corrigé lors d'un audit : le calcul de la
  courbe k-distance appelait `NearestNeighbors.kneighbors(X)` avec le jeu de données passé
  explicitement, ce qui fait que chaque point se comptait comme son propre plus proche voisin
  (distance nulle) — scikit-learn n'exclut un point de ses propres voisins que lorsque `X` est
  omis. Corrigé (`.kneighbors()` sans argument), verrouillé par
  `test_dbscan_kdistance_excludes_each_point_from_its_own_neighbors`.*
- **GMM** : alternative "molle" à K-Means, même sélection par silhouette maximale, BIC reporté à
  titre indicatif.

**Mesure réelle** (3 clients B2B à forte valeur + 80 clients réguliers) :

| Algorithme | Paramètre choisi | Silhouette | B2B isolés en "Atypique" |
|---|---:|---:|---:|
| K-Means | k=2 | 0,731 | 0/3 (fusionnés en "Fidèle") |
| GMM | k=2 | 0,731 | 0/3 (fusionnés en "Fidèle") |
| DBSCAN | ε=0,558 | 0,482 | **3/3** |

K-Means et GMM convergent vers la solution à silhouette la plus haute, qui fusionne les B2B avec
une partie des clients réguliers — DBSCAN a une silhouette globale plus basse mais isole
correctement les 3 comptes B2B, ce que les deux autres ne peuvent pas faire. Le bon algorithme
dépend de l'objectif métier : K-Means/GMM pour une partition équilibrée exploitable en
masse-marketing, DBSCAN pour repérer les comptes à traiter hors campagnes grand public.

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

Pas de ChromaDB/index vectoriel : le contexte (KPIs, alertes, segments RFM) est injecté
directement dans le prompt système — suffisant tant qu'il tient dans une poignée de lignes. Un
appel HTTP direct via `httpx` (`app/services/llm_client.py`) suffit pour chacun des 5
fournisseurs supportés (Anthropic, OpenAI, Groq, Google, OpenRouter), pas besoin de SDK dédié.
Le premier fournisseur dont la clé est configurée est utilisé automatiquement. Tout échec d'appel
(réseau, authentification, quota) bascule proprement sur la synthèse factuelle locale — jamais de
500 brut.

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

Ouvrir **http://localhost:3000** (frontend) — créer un compte au premier accès (onglet
« Créer un compte », email + mot de passe ≥ 8 caractères).

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger (tester l'API, bouton "Authorize") | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

 Port 3000 choisi pour éviter un conflit avec les plages de ports réservées par Hyper-V sur Windows (`netsh interface ipv4 show excludedportrange`). Si ce port est lui aussi pris chez vous, changez-le dans `docker-compose.yml` (service `frontend`, section `ports`).

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
| `scripts/init_db.sql` modifié mais tables inchangées | Le script ne s'exécute qu'au 1ᵉʳ démarrage du volume | `docker compose down -v` puis `up --build` |
| `POST /report/pdf` échoue en `OSError` | Image construite avant l'ajout des libs WeasyPrint | `docker compose up --build` |
| Frontend affiche du contenu périmé | Nginx sert un build statique figé | `docker compose up --build frontend` |

### 7.b. Option B — Installation locale (sans Docker)

Utile pour développer avec rechargement à chaud (`--reload`) sans reconstruire d'image à chaque
changement.

**Prérequis**
- Python 3.11+ et Node.js 18+
- PostgreSQL 14+ installé et démarré localement

**Backend**

```bash
cd copilote-ia-pme
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# éditer .env : POSTGRES_HOST=localhost, adapter DATABASE_URL en conséquence

# Créer le schéma (l'une des deux méthodes suffit, DDL identique) :
alembic upgrade head                                  # recommandé (trace versionnée)
# OU : psql -U copilote_user -d copilote_pme -f scripts/init_db.sql

uvicorn app.main:app --reload
```

L'API tourne sur **http://localhost:8000**.

**Frontend**, dans un second terminal :

```bash
cd frontend
npm install
npm run dev
```

Ouvrir **http://localhost:5173** (port du serveur de dev Vite). Le proxy intégré redirige
automatiquement `/api` et `/health` vers `http://localhost:8000` (cf. `vite.config.js`) — aucune
configuration supplémentaire, l'API doit juste tourner en parallèle.

**Raccourcis `make` équivalents**

```bash
make install         # pip install -r requirements.txt
make run              # uvicorn app.main:app --reload
make migrate          # alembic upgrade head
make ingest            # ingestion Online Retail II (cf. §1)
make simulate           # simulation stocks/trésorerie
make frontend-install    # npm ci (frontend/)
make frontend             # npm run dev
make frontend-build        # npm run build
```

---

## 8. Tests & qualité

```bash
make test              # pytest — 86 tests, SQLite en mémoire, aucune dépendance PostgreSQL
make lint              # ruff check app tests
make frontend-test     # Vitest — 13 tests (rendu jsdom, sans navigateur réel)
cd frontend && npx oxlint   # lint frontend
```

---

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

Pistes pour la Phase 5, hors périmètre actuel :
- RBAC complet (rôles/permissions différenciés par module) si le périmètre "simple" actuel s'avère insuffisant
- Ensemble pondéré ARIMA + Prophet + XGBoost pour la prévision (gain typique 5-10 %)
- Modèle de churn entraîné (régression logistique) en remplacement de l'heuristique récence-only
