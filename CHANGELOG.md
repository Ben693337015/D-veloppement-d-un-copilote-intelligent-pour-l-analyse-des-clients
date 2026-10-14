# Journal des modifications

## corrections20 — 14 octobre 2026

**Reproductibilité du rapport académique**
- Nouveau script `scripts/analyse_robustesse_previsions.py` : il recalcule le tableau 9 et la figure 10 du rapport à partir des ventes importées. Il compare les erreurs des trois modèles à deux références naïves (valeur de J-7, dernière semaine répétée), avec le MASE, sur la série complète puis sur la série arrêtée à une date donnée (`--coupure`, par défaut 2013-12-28).
- Lancement : `python -m scripts.analyse_robustesse_previsions` (Docker : `docker compose exec api python -m scripts.analyse_robustesse_previsions`).

## corrections19 — 14 octobre 2026

**Copilote : changer de fournisseur LLM en changeant seulement la clé API**
- Vérification demandée : chaque fournisseur fonctionne-t-il avec sa seule clé dans le `.env` ?
  Oui pour les 5 dans le cas simple. Quatre cas cassaient le changement de clé ; ils sont corrigés :
  1. un ancien `LLM_MODEL` d'un autre fournisseur (ex. `gemini-…` après passage à OpenAI) provoquait une erreur 404 → il est désormais ignoré, avec un avertissement ;
  2. une clé collée dans `LLM_API_KEY` était toujours envoyée à Anthropic → le fournisseur est reconnu au préfixe de la clé ;
  3. une clé collée dans la mauvaise variable était envoyée au mauvais fournisseur (401) → idem ;
  4. un espace, un retour chariot ou des guillemets autour de la clé la faisaient refuser → retirés.
- Les cas qui demandent une action sont signalés sans être devinés : plusieurs clés renseignées, `LLM_PROVIDER` sans clé correspondante ou inconnu, `LLM_BASE_URL` actif.
- Nouveau `GET /api/v1/copilot/fournisseur` (fournisseur, modèle, variable source, avertissements ; jamais la clé), bandeau en haut de la page Copilote, et ligne dans les logs au démarrage.
- `.env.example` réorganisé : les clés d'abord, les réglages facultatifs ensuite. README : tableau des préfixes ; `docker compose restart` ne relit pas le `.env`.
- 245 tests pytest (25 nouveaux : chaque fournisseur de bout en bout avec un appel simulé, URL, en-tête d'authentification, modèle), 24 tests Vitest (1 nouveau).

## corrections18 — 14 octobre 2026

**Copilote : erreur 503 « This model is currently experiencing high demand » (Gemini)**
- Surcharge temporaire côté fournisseur : le copilote basculait aussitôt sur la synthèse de secours.
- Désormais, en cas d'erreur temporaire (429, 500, 502, 503, 504) :
  1. 2 nouvelles tentatives, après 1,5 s puis 3 s (ou selon l'en-tête Retry-After, plafonné à 5 s) ;
  2. puis bascule sur un modèle de secours du même fournisseur : `gemini-3.5-flash-lite` pour Google, `claude-sonnet-5-5` pour Anthropic ;
  3. seulement ensuite la synthèse de secours, avec un message clair (« momentanément surchargé, réessayez dans quelques instants »).
- Les erreurs définitives (clé refusée, modèle introuvable) ne sont pas réessayées.
- Le modèle qui a réellement répondu (éventuellement celui de secours) s'affiche sous la réponse.
- 220 tests pytest (5 nouveaux).

## corrections17 — 13 octobre 2026

**Rapport PDF : toutes les analyses, résumées et interprétées**
- 9 sections :
  - page de garde, **résumé exécutif**, indicateurs clés ;
  - **segmentation** (comparaison des 3 algorithmes, graphique des silhouettes, interprétations) ;
  - segments et actions, avec une lecture ;
  - **clients** (concentration Pareto, meilleurs clients) ;
  - **prévision des ventes** (3 modèles, graphique, tableau, méthode, interprétations) ;
  - **trésorerie** (solde, prévision, alerte) et **stocks** (alertes).
- Plus de recalcul : le rapport lit la dernière segmentation et les prévisions en cache ; mêmes chiffres qu'à l'écran ; environ 7 s au lieu de 20 s.
- `historique_segmentation` garde aussi la comparaison des algorithmes et la synthèse :
  - nouvelles colonnes, migration Alembic `c4e8a2f17b93` ;
  - ajout automatique au démarrage pour une base existante ;
  - la synthèse est aussi reprise par le copilote.
- Une section en échec n'empêche plus le reste du rapport.
- Mise en page : numéros de page, en-têtes de tableau répétés, montants et dates au format français.
- Anciens identifiants de section acceptés.
- 215 tests pytest (5 nouveaux, qui vérifient le contenu réel du rapport et pas seulement que c'est un PDF).

## corrections16 — 13 octobre 2026

**Segmentation : les 3 algorithmes évalués automatiquement, le meilleur retenu**
- Nouveau mode `auto`, par défaut :
  - K-Means, GMM et DBSCAN sont évalués en parallèle sur la même matrice RFM ;
  - règles d'éligibilité métier (2 à 8 groupes, ≤ 10 % de bruit), puis classement par rang moyen sur silhouette, Davies-Bouldin et Calinski-Harabasz ;
  - les segments du meilleur sont enregistrés (`app/services/comparaison_segmentation.py`).
- Interprétation automatique de chaque algorithme (échelle de Kaufman & Rousseeuw) et synthèse : recommandé, écartés et pourquoi, part de clients dont l'étiquette ne dépend pas de l'algorithme.
- Page Segmentation :
  - option « Automatique (recommandé) » ;
  - synthèse, tableau comparatif, silhouettes sur l'échelle de lecture, puis une carte par algorithme avec sa courbe de diagnostic et son interprétation ;
  - tableau des clients limité en hauteur, avec défilement.
- Performance : silhouette estimée sur un échantillon fixe de 10 000 clients au-delà de ce seuil ; 100 s → 40 s sur AdventureWorks, même résultat (K-Means, k = 6).
- Délai d'attente du pipeline porté à 600 s (Nginx, interface).
- Refonte interne : le clustering d'un algorithme est factorisé dans `_clusteriser` ; le comportement d'un algorithme imposé est inchangé.
- 210 tests pytest (8 nouveaux), 23 Vitest (1 nouveau).

## corrections15 — 13 octobre 2026

**Build Docker beaucoup plus rapide**
- L'image compilait CmdStan depuis les sources (`cmdstanpy.install_cmdstan`), soit 10 à 20 minutes de build, puis supprimait le CmdStan fourni par prophet. Ce contournement compensait le problème de version de `cmdstanpy`, corrigé dans corrections13 (figé à 1.2.4).
- Étape supprimée : Prophet utilise le CmdStan déjà compilé et fourni dans son paquet.
- La vérification du build entraîne maintenant un vrai petit modèle Prophet (environ 15 s) : le build échoue tout de suite si les dépendances redeviennent incompatibles.

## corrections14 — 13 octobre 2026

**Gemini : compatibilité avec les nouvelles clés « AQ. »**
- Google AI Studio ne génère plus que des clés d'authentification « AQ. ».
- L'application passait la clé dans l'URL (`?key=`) ; elle l'envoie maintenant dans l'en-tête officiel `x-goog-api-key`, qui accepte aussi les anciennes clés « AIza ». La clé n'apparaît plus dans les URL.
- Une clé refusée (401/403) donne un message qui dit quoi vérifier.
- README §7.b, étape 3 : comment configurer la clé Gemini.
- Nouveau `.gitignore` : le projet n'en avait pas, si bien qu'un `.env` avec une clé API pouvait être publié sur GitHub.

## corrections13 — 13 octobre 2026

**README : installation sans Docker réécrite et vérifiée de bout en bout** (Python 3.11, PostgreSQL 16 vierge, Node 22)
- Étapes numérotées :
  - prérequis avec leurs versions exactes (Python 3.11 ou 3.12, pas 3.13 ; Node 20.19+ ou 22.12+) ;
  - création de l'utilisateur et de la base PostgreSQL, qui manquait ;
  - commandes Windows (PowerShell) et Linux/macOS ;
  - `.env`, `alembic upgrade head`, deux terminaux ;
  - premier usage, relance les fois suivantes, tableau de dépannage.

**Bugs trouvés en suivant la procédure, et corrigés**
- **Prophet plantait à chaque appel**, en Docker comme en local (« 'Prophet' object has no attribute 'stan_backend' »). Cause : `cmdstanpy` n'était pas figé, et la version 1.3.0 refuse le CmdStan embarqué par prophet 1.1.5. Correctif : `cmdstanpy==1.2.4`, plus un test qui entraîne réellement Prophet (les autres tests utilisaient ARIMA et ne voyaient rien).
- **L'API ne démarrait pas sans GTK/Pango** (cas courant sous Windows sans Docker) : `weasyprint` était importé au chargement. Il l'est maintenant seulement au moment de générer le PDF ; sans GTK, seul le rapport répond 503 avec la marche à suivre.
- **Menus verrouillés après un import** : la barre latérale ne relisait l'état des données qu'au changement de page, alors que les liens verrouillés empêchaient justement d'en changer. L'import réussi la met maintenant à jour immédiatement.
- 201 tests pytest, 22 Vitest.

## corrections12 — 13 octobre 2026

**Le copilote a accès à toutes les données de l'application**
- Nouvelle table `historique_segmentation` : une ligne par segmentation, avec k, silhouette, Davies-Bouldin, Calinski-Harabasz, diagnostic par k et répartition des segments. Avant, ces métriques étaient perdues après affichage.
  - Créée par `init_db.sql`, par la migration Alembic `b7d3e9a41c20`, et au démarrage de l'API si elle manque : une base existante n'a pas besoin d'être réinitialisée.
- Contexte du copilote :
  - les prévisions des ventes des 3 modèles avec leur interprétation (avant : ARIMA seul), détaillées par semaine pour le modèle le plus fiable ;
  - la dernière segmentation et son diagnostic.
- SQL :
  - jusqu'à 3 requêtes par question pour croiser plusieurs sources ; seule une requête en échec est corrigée ;
  - accès à `historique_segmentation` ;
  - e-mails des clients sur option (`COPILOTE_ACCES_EMAILS`, désactivé par défaut) ; mots de passe jamais.
- Interface : toutes les requêtes exécutées s'affichent sous la réponse.
- Vérifié sur PostgreSQL avec AdventureWorks, via le rôle `copilote_lecture`.
- 199 tests pytest (11 nouveaux), 21 Vitest (1 nouveau).

## corrections11 — 13 octobre 2026

**Prévision des ventes : les 3 modèles en arrière-plan, avec graphiques interprétés**
- Nouvel endpoint `GET /analytics/forecast/sales/comparaison` (`app/services/comparaison_previsions.py`).
- Calcul : Prophet, ARIMA et XGBoost tournent en parallèle ; le résultat est mis en cache tant que les ventes ne changent pas.
- Pré-calcul automatique après chaque import de ventes (réglage `PREVISIONS_PRECALCUL`).
- Interprétation automatique de chaque modèle : niveau prévu, tendance, fiabilité, intervalle de confiance.
- Synthèse : modèle le plus fiable, concordance ou divergence des modèles, rupture récente des ventes.
- Page Ventes & Stocks : plus de choix de modèle ni de bouton. À l'ouverture : synthèse, graphique comparatif, puis un graphique interprété par modèle ; horizon de 14 à 90 jours.
- Tableau des alertes de stock limité en hauteur, avec défilement : ses 130 lignes repoussaient la prévision tout en bas de la page.
- Mesuré sur PostgreSQL avec AdventureWorks : 3,2 à 4,2 s pour le premier calcul, puis instantané.
- 188 tests pytest (14 nouveaux), 20 Vitest (3 nouveaux).

## corrections10 — 13 octobre 2026

**Le copilote a accès à la base** (`app/services/sql_agent.py`)
- Avec un LLM configuré, le copilote écrit une requête SQL pour répondre à la question. L'application la vérifie, l'exécute en lecture seule, et le LLM répond à partir des lignes réelles.
- En cas de refus ou d'erreur SQL, le LLM reçoit l'erreur et corrige une fois. Il répond `AUCUNE` si la question n'a pas besoin de la base.
- Sécurité :
  - analyse sqlglot : une seule instruction de lecture, tables en liste blanche, ni `users`, ni `email`, ni `SELECT *`, ni fonctions système ;
  - transaction `READ ONLY` avec délai maximal de 5 s ;
  - rôle PostgreSQL optionnel `copilote_lecture`, créé par `scripts/init_db.sql` : `SELECT` seulement, colonne par colonne ;
  - réglages dans `.env` : `COPILOTE_ACCES_BASE` et `COPILOTE_DATABASE_URL_LECTURE`.
- Interface : sous chaque réponse, « Données lues dans la base (n lignes) — voir la requête ».
- Testé sur PostgreSQL 16 avec AdventureWorks : 6 questions réelles, de 1,2 à 1,9 s hors temps du LLM.
- Nouvelle dépendance : `sqlglot` (pur Python).
- 174 tests pytest (39 nouveaux), 17 Vitest (2 nouveaux).

**Documentation** : le paragraphe « Ajouts du 12/10 » du README, égaré dans la section Installation, est replacé dans la section Copilote. L'affirmation « le détail des transactions reste hors de portée du copilote » est corrigée.

## corrections9 — 13 octobre 2026

**Copilote : erreur 404 « models/gemini-1.5-flash is not found »**
- Cause : les modèles par défaut avaient été retirés par les fournisseurs. Toute question tombait donc en mode secours.
- Nouveaux défauts : Anthropic `claude-haiku-5-5` (Claude 3.5 Haiku retiré le 19/02/2026), Groq `openai/gpt-oss-20b` (Llama 3.1 8B retiré le 16/08/2026), Google `gemini-3.6-flash`, OpenRouter `openrouter/free`. OpenAI `gpt-4o-mini` est inchangé (toujours actif).
- Plafond de réponse porté de 500 à 1 500 jetons : les modèles récents raisonnent avant de répondre et ces jetons sont décomptés du même plafond. Délai d'appel porté de 20 à 30 s.
- Une erreur 404 indique désormais « modèle introuvable ou retiré : renseigner LLM_MODEL ».

**Copilote : « quels clients font mon chiffre d'affaires ? »**
- Le contexte contient maintenant le top 10 des clients par CA (tout l'historique, retours exclus), leur part du CA, leur segment et leur récence. Il contient aussi la concentration du CA : combien de clients font 80 % du CA (`analytics_service.get_top_clients_ca`).
- Les exemples de clients cités par segment sont triés par montant décroissant. Avant, l'échantillon était arbitraire et changeait d'un appel à l'autre.
- 135 tests pytest (6 nouveaux).

## corrections7 — 12 octobre 2026

**Segmentation (audit du 9 octobre, étape 1 — réintégrée)**
- Étiquetage client par client : cycle de vie (Nouveau / À risque / Perdu) par règle absolue de récence, niveau de valeur (Fidèle / Occasionnel) par profil du cluster et scores individuels.
- Nouveau segment « Perdu », y compris pour les clients sans achat dans la fenêtre (ils gardaient leur ancien segment).
- Churn absolu = récence / fenêtre (au lieu d'un rang relatif) ; ex aequo départagés par rang moyen ; log1p sur F et M.
- Bruit DBSCAN « Atypique » seulement si actif et M ≥ 4.
- Segmentation reproductible : tri déterministe avant clustering (les mêmes données donnaient k = 2 ou k = 6).

**Test de bout en bout** (`scripts/e2e/`, navigateur réel + PostgreSQL + Nginx + LLM simulé)
- API figée pendant un import (routes `async` exécutant du code bloquant) : traitement déplacé dans un thread.
- Import groupé : 70 s → 15 s ; écriture des segments groupée : 30 s → < 1 s (60 398 lignes, PostgreSQL).
- Copilote : `LLM_BASE_URL` (LLM local, proxy, serveur simulé), origine de chaque réponse (`mode` : llm / local / secours) affichée, contexte des alertes de stock borné aux 25 plus urgentes, question vide ou > 2 000 caractères refusée.
- Alertes de stock triées par urgence ; page Segmentation : débordement corrigé ; churn arrondi dans le contexte.

**Documentation** : README §6 (mesures réelles sur AdventureWorks, anciens benchmarks non reproductibles retirés), §7 (port 3000), §8 (128 tests pytest, 15 Vitest, test de bout en bout) ; note technique PDF ; audit mis à jour.

## corrections5 — 10 octobre 2026
- Nginx : limite d'envoi portée à 51 Mo (erreur « Request Entity Too Large » sur un fichier de 7 Mo) et délai de 600 s pour les imports ; messages d'erreur 413/502/504 en français.

## corrections4 — 10 octobre 2026
- Détection automatique : prix unitaire distinct du montant total (CA d'Online Retail II divisé par la quantité).
- `load_online_retail` accepte les en-têtes réels du fichier (Invoice, Price, Customer ID).
- Message de plafonnement aligné sur le calcul réel (min(P99, Q3 + 1,5×IQR)).

## corrections3 — 10 octobre 2026
- Jeu complet AdventureWorks (Microsoft, MIT) : `python -m app.ingestion.export_adventureworks`.
- Déduplication incluant le n° de facture ; compteur « mis à jour » corrigé.

## corrections2 — 10 octobre 2026
- Export Wide World Importers (`app/ingestion/export_wwi.py`, `scripts/wwi/`).
- Retours : seules les conventions réelles (C/A + chiffre, AV/AVOIR) au lieu de tout n° commençant par C ou A.
