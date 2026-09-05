"""Service métier — Calcul RFM & clustering (sous-projet Abdoulmadjid).

Cette pièce manquait jusqu'ici : `marketing_service.py` supposait que les
colonnes `clients.segment_rfm/score_r/score_f/score_m/cluster_id/score_churn`
étaient déjà remplies par "un job hors-ligne (notebook ou script)". Ce
module EST ce job — exposé via l'API pour pouvoir être déclenché depuis le
Dashboard (bouton "Générer" / "Pipeline RFM", cf. capture d'écran fournie :
badge "Pipeline RFM · 2 974 clients · k=2 · Sil.=0.408").

Méthodologie (cf. skill `pme-marketing-rfm` et README §5) :
  1. Calcul R/F/M par client sur une fenêtre glissante (RFM_ANALYSIS_WINDOW_DAYS),
     ancrée sur la date de transaction la plus récente en base (et non
     `datetime.utcnow()`, sans quoi un dataset historique comme Online Retail II
     donnerait des récences de plusieurs milliers de jours).
  2. Retours (`est_retour=True`) exclus du calcul (cohérent avec
     analytics_service qui les exclut déjà du CA).
  3. Scores R/F/M en quintiles (1 à 5) via un classement (`rank`) plutôt que
     `pd.qcut` brut — évite l'échec silencieux de `qcut` sur des données à
     faible variance/doublons (bug documenté dans le skill pme-marketing-rfm).
  4. Standardisation z-score des valeurs R/F/M brutes avant K-Means (sinon
     le Montant écrase la Récence dans le calcul de distance).
  5. Diagnostic coude (inertie) + score de silhouette pour k = 2..6 (mêmes
     bornes que l'écran "Coude K-Means" fourni en exemple).
  6. Choix de k par silhouette MAXIMALE (pas uniquement le coude visuel) —
     cohérent avec le constat déjà documenté sur données réelles Online
     Retail II : k=2 gagne souvent face à k=3 malgré un coude visuel à 3,
     à cause d'un petit segment B2B/grossiste qui domine la variance.
  7. Étiquetage des segments par seuils absolus par axe R/F/M (Fidèle = R
     haut ET F haut ET M haut ; À risque = récurrent ET R bas sur un seuil
     ABSOLU de jours d'inactivité, PAS un simple classement relatif entre
     clusters — cf. `_assign_segments` pour la justification détaillée du
     bug que ce choix corrige), pas par position arbitraire du cluster_id —
     reste valide quel que soit k.
  8. `score_churn` : heuristique basée sur la récence normalisée (PAS un
     modèle de classification entraîné). À documenter comme tel dans le
     rapport (cf. Cadrage Tâche #10, traçabilité méthodologique) ; un vrai
     modèle de churn (régression logistique sur RFM + ancienneté) est une
     amélioration future (Phase 5), volontairement hors périmètre ici pour
     ne pas présenter un modèle non entraîné comme validé.

Algorithmes de clustering disponibles (`algorithme` : "kmeans" | "dbscan" |
"gmm") — cf. README §6 pour la justification :
  - "kmeans" (défaut) : partition tous les clients en k groupes de tailles
    comparables. Sur Online Retail II réel, k=2 gagne souvent (silhouette
    max) au prix d'écraser un petit groupe B2B/grossiste dans le même
    cluster qu'une partie de la clientèle "normale" — K-Means DOIT classer
    chaque point, il ne peut pas dire "celui-ci n'appartient à aucun
    groupe cohérent".
  - "dbscan" : basé sur la densité, PAS de k à choisir. Contrairement à
    K-Means qui DOIT classer chaque point (il ne peut pas dire "celui-ci
    n'appartient à aucun groupe cohérent"), DBSCAN a deux façons de gérer
    une minorité de clients atypiques (B2B/grossistes) : (a) si le groupe
    est plus petit que `min_samples`, aucun de ses points ne peut devenir
    "core point" -> il est classé "bruit" (label -1), étiqueté
    `SEGMENT_ATYPIQUE` par `_assign_segments`, un segment que K-Means ne
    peut pas produire ; (b) si le groupe est assez cohésif et nombreux, il
    peut former son propre cluster dense, séparé du reste SANS
    contamination croisée, sans qu'aucun k n'ait dû être choisi à l'avance.
    Dans les deux cas, le résultat est plus honnête que K-Means, qui force
    une partition en groupes de tailles comparables. epsilon est
    auto-sélectionné par détection de coude sur la courbe k-distance (cf.
    `_detecter_coude`) plutôt que fixé arbitrairement.
  - "gmm" : Gaussian Mixture Model, alternative "molle" à K-Means (chaque
    client a une probabilité d'appartenance à chaque composante, pas une
    affectation dure) — utile quand les segments RFM se chevauchent plutôt
    que d'être clairement séparés. Choix du nombre de composantes par
    silhouette maximale (mêmes bornes k_min/k_max que K-Means), BIC reporté
    à titre indicatif (critère de sélection de modèle standard pour les GMM,
    plus bas = meilleur, à ne pas confondre avec l'inertie K-Means).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.mixture import GaussianMixture
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.preprocessing import winsoriser as _winsoriser_partage
from app.models import Client, Transaction

ALGORITHMES_DISPONIBLES = {"kmeans", "dbscan", "gmm"}
SEGMENT_NOUVEAU = "Nouveau"
SEGMENT_FIDELE = "Fidèle"
SEGMENT_RISQUE = "À risque"
SEGMENT_OCCASIONNEL = "Occasionnel"
SEGMENT_ATYPIQUE = "Atypique (B2B/grossiste)"


@dataclass
class DiagnosticPoint:
    k: int
    inertie: float
    silhouette: float | None
    # Champs supplémentaires, utilisés seulement par DBSCAN — None pour
    # K-Means/GMM. Cf. docstring module pour la sémantique de réutilisation
    # de `k`/`inertie` selon l'algorithme (index de candidat/epsilon testé).
    n_clusters_trouves: int | None = None
    n_bruit: int | None = None


@dataclass
class RFMPipelineResult:
    n_clients: int
    fenetre_jours: int
    date_reference: datetime | None
    algorithme_utilise: str = "kmeans"
    diagnostics: list[DiagnosticPoint] = field(default_factory=list)
    k_choisi: int | None = None
    eps_choisi: float | None = None
    silhouette_choisi: float | None = None
    ecrit_en_base: bool = False
    avertissements: list[str] = field(default_factory=list)


def _reference_date(db: Session) -> datetime | None:
    """Date de référence pour le calcul de récence : la transaction la plus
    récente en base, PAS la date système (données historiques comme Online
    Retail II datent de 2010-2011 ; utiliser `datetime.utcnow()` donnerait
    des récences absurdes de plusieurs milliers de jours)."""
    return db.query(func.max(Transaction.date_transaction)).scalar()


def compute_rfm_dataframe(db: Session, fenetre_jours: int | None = None) -> tuple[pd.DataFrame, datetime | None]:
    """Calcule R/F/M brut par client sur la fenêtre d'analyse."""
    fenetre_jours = fenetre_jours or settings.RFM_ANALYSIS_WINDOW_DAYS
    date_ref = _reference_date(db)
    if date_ref is None:
        return pd.DataFrame(columns=["client_id", "recence_jours", "frequence_achats", "montant_total"]), None

    date_debut = date_ref - pd.Timedelta(days=fenetre_jours)

    lignes = (
        db.query(
            Transaction.client_id,
            Transaction.date_transaction,
            Transaction.numero_facture,
            Transaction.quantite,
            Transaction.prix_unitaire,
        )
        .filter(
            ~Transaction.est_retour,
            Transaction.client_id.is_not(None),
            Transaction.date_transaction >= date_debut,
            Transaction.date_transaction <= date_ref,
        )
        .all()
    )
    if not lignes:
        return pd.DataFrame(columns=["client_id", "recence_jours", "frequence_achats", "montant_total"]), date_ref

    df = pd.DataFrame(
        lignes, columns=["client_id", "date_transaction", "numero_facture", "quantite", "prix_unitaire"]
    )
    df["montant_ligne"] = df["quantite"].astype(float) * df["prix_unitaire"].astype(float)

    agg = df.groupby("client_id").agg(
        derniere_date=("date_transaction", "max"),
        # Fréquence = nb de factures distinctes si disponible, sinon nb de lignes
        # (une facture peut contenir plusieurs lignes produit).
        frequence_achats=("numero_facture", lambda s: s.nunique() if s.notna().any() else len(s)),
        montant_total=("montant_ligne", "sum"),
    )
    agg["recence_jours"] = (date_ref - agg["derniere_date"]).dt.days
    agg = agg.drop(columns=["derniere_date"]).reset_index()
    return agg, date_ref


def _score_quintile(serie: pd.Series, inverse: bool = False) -> pd.Series:
    """Score 1-5 par PERCENTILE DE RANG plutôt que `qcut` brut ou un nombre
    de bins réduit — robuste aux doublons et aux petits jeux de données PME
    (cf. skill pme-marketing-rfm §1).

    BUG CORRIGÉ : la version précédente utilisait `n_bins = min(5,
    serie.nunique())`, ce qui réduisait l'échelle à 1-2 (au lieu de 1-5) dès
    que la variable n'avait que 2 valeurs distinctes — un cas très courant
    pour `frequence_achats` (souvent un petit entier). Un cluster de "gros"
    clients pouvait alors se retrouver avec un score_f de 2/2 (donc sous
    tout seuil calibré sur 1-5), alors qu'il s'agissait bien de la valeur la
    PLUS ÉLEVÉE de la variable. Détecté en vérifiant le pipeline sur un
    scénario B2B + clientèle régulière : les gros comptes B2B (fréquence=8,
    seule autre valeur=6) étaient éligibles "Fidèle" sur R et M mais jamais
    sur F, à cause de ce plafonnement artificiel.
    Le rang en PERCENTILE (0-1) est toujours continu, quel que soit le
    nombre de valeurs distinctes de la variable brute (les égalités sont
    départagées par ordre via `method="first"`), donc `ceil(percentile * 5)`
    retombe toujours sur une échelle 1-5 complète et comparable entre axes
    R/F/M — condition nécessaire pour les seuils absolus de `_assign_segments`.
    """
    if serie.nunique() < 2:
        return pd.Series(3, index=serie.index)  # une seule valeur -> score neutre
    rangs_percentile = serie.rank(method="first", ascending=not inverse, pct=True)
    scores = np.ceil(rangs_percentile * 5).clip(lower=1, upper=5).astype(int)
    return scores


def winsoriser(serie: pd.Series, percentile: float = 99.0) -> tuple[pd.Series, int]:
    """Ré-exporté depuis `app.core.preprocessing` pour compatibilité
    ascendante des imports existants (`from app.services.rfm_clustering_service
    import winsoriser`) — l'implémentation réelle est partagée avec
    `forecasting_service.py` (cf. `app/core/preprocessing.py`)."""
    return _winsoriser_partage(serie, percentile)


def run_kmeans_diagnostics(
    rfm_scaled: np.ndarray, k_min: int = 2, k_max: int = 6
) -> list[DiagnosticPoint]:
    """Reproduit l'écran "Coude K-Means" : inertie + silhouette pour chaque k."""
    n_samples = rfm_scaled.shape[0]
    points: list[DiagnosticPoint] = []
    k_max_effectif = min(k_max, max(k_min, n_samples - 1))

    for k in range(k_min, k_max_effectif + 1):
        if k >= n_samples:
            break
        modele = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = modele.fit_predict(rfm_scaled)
        silhouette = (
            float(silhouette_score(rfm_scaled, labels))
            if len(set(labels)) > 1 and n_samples > k
            else None
        )
        points.append(DiagnosticPoint(k=k, inertie=float(modele.inertia_), silhouette=silhouette))
    return points


def choisir_k(diagnostics: list[DiagnosticPoint]) -> int | None:
    """Choix de k par silhouette MAXIMALE (documenté §module docstring).

    Volontairement PAS "le coude visuel" seul : sur données RFM réelles, la
    silhouette la plus haute ne coïncide pas toujours avec le coude
    d'inertie (cf. constat Online Retail II : k=2 optimal malgré un coude
    apparent à k=3 — un petit segment B2B/grossiste domine la variance).
    Réutilisée telle quelle pour GMM (même critère, `k` = n_components)."""
    candidats = [p for p in diagnostics if p.silhouette is not None]
    if not candidats:
        return diagnostics[0].k if diagnostics else None
    return max(candidats, key=lambda p: p.silhouette).k


def run_gmm_diagnostics(
    rfm_scaled: np.ndarray, k_min: int = 2, k_max: int = 6
) -> list[DiagnosticPoint]:
    """Équivalent du diagnostic coude/silhouette K-Means, pour un Gaussian
    Mixture Model. `inertie` accueille le BIC (Bayesian Information
    Criterion, plus BAS = meilleur — à l'inverse de l'inertie K-Means, plus
    HAUTE = pire) : reporté à titre indicatif seulement, la sélection finale
    reste basée sur la silhouette (cf. `choisir_k`) pour rester comparable
    entre algorithmes."""
    n_samples = rfm_scaled.shape[0]
    points: list[DiagnosticPoint] = []
    k_max_effectif = min(k_max, max(k_min, n_samples - 1))

    for k in range(k_min, k_max_effectif + 1):
        if k >= n_samples:
            break
        modele = GaussianMixture(n_components=k, random_state=42, n_init=5)
        labels = modele.fit_predict(rfm_scaled)
        silhouette = (
            float(silhouette_score(rfm_scaled, labels))
            if len(set(labels)) > 1 and n_samples > k
            else None
        )
        points.append(DiagnosticPoint(k=k, inertie=float(modele.bic(rfm_scaled)), silhouette=silhouette))
    return points


def _detecter_coude(valeurs_triees: np.ndarray) -> int:
    """Détecte l'indice du "coude" d'une courbe triée croissante par la
    méthode de la distance maximale à la diagonale reliant le premier et le
    dernier point — approche géométrique standard pour l'auto-sélection
    d'epsilon en DBSCAN à partir de la courbe k-distance (cf. Rahmah &
    Sitanggang, 2016 ; principe analogue à l'algorithme Kneedle de Satopaa
    et al., 2011). Évite de fixer epsilon arbitrairement/à la main."""
    n = len(valeurs_triees)
    if n < 3:
        return n - 1
    x = np.arange(n, dtype=float)
    y = valeurs_triees.astype(float)
    etendue_x = x[-1] - x[0] or 1e-9
    etendue_y = y[-1] - y[0] or 1e-9
    x_norm = (x - x[0]) / etendue_x
    y_norm = (y - y[0]) / etendue_y
    # Une fois x et y normalisés sur [0,1], la distance à la diagonale
    # y=x est proportionnelle à |y_norm - x_norm| — le facteur 1/sqrt(2)
    # constant n'affecte pas l'indice qui maximise cette distance.
    distances = np.abs(y_norm - x_norm)
    return int(np.argmax(distances))


def run_dbscan_diagnostics(
    rfm_scaled: np.ndarray, min_samples: int = 5, n_candidats: int = 8
) -> tuple[list[DiagnosticPoint], float | None]:
    """Diagnostic DBSCAN : pas de "k" à balayer (à la différence de
    K-Means/GMM), donc on balaie plutôt une plage de valeurs d'epsilon
    dérivée de la courbe k-distance (distance de chaque point à son
    `min_samples`-ième plus proche voisin, triée croissante) — le "coude"
    de cette courbe est le epsilon usuellement recommandé dans la
    littérature DBSCAN (cf. `_detecter_coude`).

    Réutilisation des champs de `DiagnosticPoint` : `k` = index séquentiel
    du candidat (1..N, croissant avec epsilon testé — permet de tracer le
    même type de graphique "coude" que K-Means côté frontend), `inertie` =
    epsilon testé, `n_clusters_trouves`/`n_bruit` = résultat de ce candidat.

    Retourne (diagnostics, epsilon_du_coude) — ce dernier sert de solution
    de repli si aucun candidat balayé ne produit ≥2 clusters exploitables.
    """
    n_samples = rfm_scaled.shape[0]
    min_samples_effectif = min(min_samples, max(2, n_samples - 1))
    if n_samples <= min_samples_effectif + 1:
        return [], None

    voisins = NearestNeighbors(n_neighbors=min_samples_effectif).fit(rfm_scaled)
    # BUG CORRIGÉ : appeler .kneighbors(rfm_scaled) en passant explicitement
    # le jeu de données (au lieu de .kneighbors() sans argument) fait que
    # chaque point se compte comme son propre plus proche voisin (distance
    # 0, colonne 0) — vérifié : scikit-learn n'exclut un point de ses
    # propres voisins QUE lorsque `X` est omis (self-query implicite), pas
    # lorsqu'il est passé explicitement, même si c'est le même tableau que
    # celui utilisé pour l'entraînement. Conséquence AVANT correction :
    # `distances[:, -1]` valait la distance au (min_samples-1)-ième voisin
    # RÉEL, pas au min_samples-ième comme documenté et comme l'exige la
    # méthode de sélection d'epsilon (Ester et al., 1996) — decalage d'un
    # cran qui biaisait systématiquement epsilon (et donc le clustering
    # DBSCAN réellement exécuté, pas seulement l'affichage du diagnostic)
    # vers des valeurs trop petites.
    distances, _ = voisins.kneighbors()
    k_distances = np.sort(distances[:, -1])

    indice_coude = _detecter_coude(k_distances)
    eps_coude = float(k_distances[indice_coude])
    if eps_coude <= 0:
        # Beaucoup de clients strictement identiques après standardisation
        # (données synthétiques très répétitives) -> epsilon nul rendrait
        # DBSCAN inutilisable (tout devient "bruit"). Repli sur la médiane.
        eps_coude = float(np.median(k_distances[k_distances > 0])) if (k_distances > 0).any() else 0.1

    percentiles_cibles = np.linspace(0.5, 0.98, n_candidats)
    eps_candidats = sorted(
        ({float(np.percentile(k_distances, p * 100)) for p in percentiles_cibles} | {eps_coude}) - {0.0}
    )

    points: list[DiagnosticPoint] = []
    for i, eps in enumerate(eps_candidats, start=1):
        modele = DBSCAN(eps=eps, min_samples=min_samples_effectif)
        labels = modele.fit_predict(rfm_scaled)
        masque_non_bruit = labels != -1
        n_clusters = len(set(labels[masque_non_bruit]))
        n_bruit = int((labels == -1).sum())
        silhouette = None
        if n_clusters > 1 and masque_non_bruit.sum() > n_clusters:
            silhouette = float(silhouette_score(rfm_scaled[masque_non_bruit], labels[masque_non_bruit]))
        points.append(
            DiagnosticPoint(
                k=i, inertie=eps, silhouette=silhouette, n_clusters_trouves=n_clusters, n_bruit=n_bruit
            )
        )
    return points, eps_coude


def choisir_eps(diagnostics: list[DiagnosticPoint], eps_repli: float | None) -> float | None:
    """Choix d'epsilon par silhouette maximale parmi les candidats ayant
    produit ≥2 clusters exploitables (silhouette non calculable sinon) —
    même principe que `choisir_k`. Repli sur `eps_repli` (le coude détecté
    par `_detecter_coude`) si aucun candidat balayé n'est exploitable."""
    candidats = [p for p in diagnostics if p.silhouette is not None]
    if not candidats:
        return eps_repli
    return max(candidats, key=lambda p: p.silhouette).inertie


def _assign_segments(df: pd.DataFrame, fenetre_jours: int) -> pd.Series:
    """Étiquette chaque cluster récurrent (skill `pme-marketing-rfm` §4) :
      - Fidèle    : R haut ET F haut ET M haut (les trois, sur score
        percentile — cf. `_score_quintile`).
      - À risque  : R bas — un client RÉCURRENT (donc pas "Nouveau") qui
        n'a rien acheté depuis plus de la moitié de la fenêtre d'analyse.
      - Occasionnel : le reste (actif récemment, mais pas "excellent" sur
        F/M).

    BUG CORRIGÉ (2e itération, détecté par un scénario avec 3 profils
    fidèle/à risque/nouveau) : la version précédente exigeait aussi "F haut
    OU M haut" (score percentile) pour qualifier "À risque". Or ce score est
    calculé sur TOUTE la population, clients ponctuels ("Nouveau") inclus :
    quand la population contient à la fois un groupe VIP (F/M très hauts) et
    un groupe de nouveaux clients à un seul achat (F/M cette fois très bas
    par construction), un cluster de clients dormants mais moyennement bons
    (ex. 3 achats espacés, montants modestes) se retrouve mathématiquement
    au MILIEU du classement percentile — sous le seuil "haut" alors même
    qu'il s'agit bien de clients réguliers désormais silencieux. Le rang
    percentile est en effet dépendant de la FORME de la distribution
    globale, pas seulement de la position relative entre clusters.

    SOLUTION : "R bas" utilise désormais un seuil ABSOLU (jours écoulés
    depuis le dernier achat > moitié de `fenetre_jours`), pas un rang
    relatif — cohérent avec la pratique RFM courante ("6 mois d'inactivité"
    pour une fenêtre d'analyse type 12 mois) et insensible à la forme de la
    distribution. Un client récurrent qui a dépassé ce seuil est
    systématiquement "À risque", quel que soit son F/M historique : le
    signal métier pertinent est "c'était un client qui revenait, il ne
    revient plus", pas "c'était un très gros client". Fidèle reste, lui,
    conditionné aux trois scores hauts (F/M haut ET actif récemment).

    BUG ÉVITÉ (inchangé) : calculer ce classement sur TOUS les clients (y
    compris les acheteurs ponctuels à une seule commande) peut faire porter
    une étiquette à un cluster dominé par des acheteurs ponctuels récents,
    et non aux clients réellement dormants. On calcule donc les moyennes
    UNIQUEMENT sur les clients récurrents (frequence_achats > 1), et on
    applique la règle "Nouveau" seulement après.

    DBSCAN uniquement : cluster_id == -1 ("bruit", points de faible densité)
    est systématiquement étiqueté `SEGMENT_ATYPIQUE`, quel que soit son
    F/M/récurrence — c'est précisément le signal recherché (isoler les
    profils B2B/grossistes que K-Means force à tort dans un cluster
    "normal"). Cette règle prime sur "Nouveau" : un client qui n'a acheté
    qu'une fois mais avec un profil statistiquement isolé (ex. très grosse
    commande unique) est plus informatif classé "Atypique" que "Nouveau".
    """
    SEUIL_SCORE = 3.0
    SEUIL_JOURS_RISQUE = fenetre_jours / 2
    df = df.copy()
    recurrents = df[df["frequence_achats"] > 1]

    if recurrents.empty:
        # Tous les clients sont des acheteurs ponctuels : aucun classement
        # Fidèle/À risque/Occasionnel n'est pertinent, la règle "Nouveau"
        # s'appliquera de toute façon à tout le monde ci-dessous.
        mapping = dict.fromkeys(df["cluster_id"].unique(), SEGMENT_OCCASIONNEL)
    else:
        stats = recurrents.groupby("cluster_id").agg(
            recence_moyenne=("recence_jours", "mean"),
            score_f_moyen=("score_f", "mean"),
            score_m_moyen=("score_m", "mean"),
        )
        mapping = {}
        for cluster_id, ligne in stats.iterrows():
            r_bas = ligne["recence_moyenne"] > SEUIL_JOURS_RISQUE
            f_haut = ligne["score_f_moyen"] >= SEUIL_SCORE
            m_haut = ligne["score_m_moyen"] >= SEUIL_SCORE
            if not r_bas and f_haut and m_haut:
                mapping[cluster_id] = SEGMENT_FIDELE
            elif r_bas:
                mapping[cluster_id] = SEGMENT_RISQUE
            else:
                mapping[cluster_id] = SEGMENT_OCCASIONNEL
        # Un cluster entièrement composé d'acheteurs ponctuels n'apparaît pas
        # dans `recurrents` : lui donner un label par défaut sûr (il sera de
        # toute façon réécrit en "Nouveau" pour chacun de ses membres).
        for cluster_id in df["cluster_id"].unique():
            mapping.setdefault(cluster_id, SEGMENT_OCCASIONNEL)

    segments = df["cluster_id"].map(mapping)
    segments = segments.where(df["frequence_achats"] > 1, SEGMENT_NOUVEAU)
    # Dernière passe, prioritaire sur tout le reste : bruit DBSCAN -> Atypique.
    segments = segments.where(df["cluster_id"] != -1, SEGMENT_ATYPIQUE)
    return segments


def run_full_pipeline(
    db: Session,
    k_min: int = 2,
    k_max: int = 6,
    fenetre_jours: int | None = None,
    winsor_percentile: float | None = None,
    dry_run: bool = False,
    algorithme: str = "kmeans",
) -> RFMPipelineResult:
    """Point d'entrée principal : calcule RFM, PRÉTRAITE (winsorizing) les
    valeurs utilisées pour le clustering, diagnostics coude/silhouette,
    choisit k (ou epsilon pour DBSCAN), segmente, et (sauf `dry_run=True`)
    écrit en base.

    `algorithme` : "kmeans" (défaut), "dbscan" ou "gmm" — cf. docstring
    module pour la justification de chacun.

    `dry_run=True` sert à afficher le graphique coude/silhouette (écran
    "Graphiques") sans modifier les segments déjà en base — utile pour
    explorer différentes fenêtres d'analyse avant de valider un import.
    """
    if algorithme not in ALGORITHMES_DISPONIBLES:
        raise ValueError(
            f"Algorithme inconnu : '{algorithme}' (attendu : {sorted(ALGORITHMES_DISPONIBLES)})"
        )

    fenetre_jours = fenetre_jours or settings.RFM_ANALYSIS_WINDOW_DAYS
    winsor_percentile = winsor_percentile or settings.RFM_WINSOR_PERCENTILE
    rfm_df, date_ref = compute_rfm_dataframe(db, fenetre_jours)

    if rfm_df.empty:
        return RFMPipelineResult(
            n_clients=0,
            fenetre_jours=fenetre_jours,
            date_reference=date_ref,
            algorithme_utilise=algorithme,
            avertissements=["Aucune transaction exploitable sur la fenêtre d'analyse."],
        )

    avertissements: list[str] = []
    if len(rfm_df) < 2 * k_min:
        avertissements.append(
            f"Peu de clients ({len(rfm_df)}) pour k={k_min}..{k_max} : les résultats de clustering "
            "seront peu fiables (à mentionner explicitement dans le rapport, cf. limites/biais)."
        )

    rfm_df["score_r"] = _score_quintile(rfm_df["recence_jours"], inverse=True)  # récent = score haut
    rfm_df["score_f"] = _score_quintile(rfm_df["frequence_achats"])
    rfm_df["score_m"] = _score_quintile(rfm_df["montant_total"])

    # PRÉTRAITEMENT avant clustering : plafonnement des valeurs extrêmes
    # (montant, fréquence) UNIQUEMENT pour le calcul de distance K-Means —
    # `rfm_df["montant_total"]`/`["frequence_achats"]` (agrégats réels,
    # utilisés pour l'écriture en base et le reporting) restent inchangés.
    montant_clustering, n_montant_plafonne = winsoriser(rfm_df["montant_total"], winsor_percentile)
    frequence_clustering, n_frequence_plafonnee = winsoriser(rfm_df["frequence_achats"], winsor_percentile)
    if n_montant_plafonne:
        avertissements.append(
            f"{n_montant_plafonne} client(s) au montant plafonné à P{winsor_percentile:.0f} "
            "avant clustering (valeurs extrêmes neutralisées pour le K-Means uniquement — "
            "les montants réels stockés en base ne sont pas modifiés)."
        )
    if n_frequence_plafonnee:
        avertissements.append(
            f"{n_frequence_plafonnee} client(s) à la fréquence plafonnée à P{winsor_percentile:.0f} "
            "avant clustering (même principe que pour le montant)."
        )

    scaler = StandardScaler()
    rfm_scaled = scaler.fit_transform(
        pd.DataFrame(
            {
                "recence_jours": rfm_df["recence_jours"],
                "frequence_achats": frequence_clustering,
                "montant_total": montant_clustering,
            }
        )
    )

    k_choisi: int | None = None
    eps_choisi: float | None = None

    if algorithme == "dbscan":
        diagnostics, eps_repli = run_dbscan_diagnostics(rfm_scaled)
        if not diagnostics:
            avertissements.append("Pas assez de clients pour exécuter DBSCAN.")
            return RFMPipelineResult(
                n_clients=len(rfm_df),
                fenetre_jours=fenetre_jours,
                date_reference=date_ref,
                algorithme_utilise=algorithme,
                diagnostics=[],
                avertissements=avertissements,
            )
        eps_choisi = choisir_eps(diagnostics, eps_repli)
        candidat_choisi = min(diagnostics, key=lambda p: abs(p.inertie - eps_choisi))
        silhouette_choisi = candidat_choisi.silhouette
        min_samples_effectif = min(5, max(2, len(rfm_df) - 1))
        modele_final = DBSCAN(eps=eps_choisi, min_samples=min_samples_effectif)
        rfm_df["cluster_id"] = modele_final.fit_predict(rfm_scaled)
        n_atypiques = int((rfm_df["cluster_id"] == -1).sum())
        if n_atypiques:
            avertissements.append(
                f"{n_atypiques} client(s) isolé(s) comme atypique(s) par DBSCAN (segment "
                f"'{SEGMENT_ATYPIQUE}') — probablement des profils B2B/grossistes que K-Means "
                "aurait forcés dans un cluster 'normal' (cf. README §6)."
            )
        if silhouette_choisi is None:
            avertissements.append(
                "Aucune valeur d'epsilon testée n'a produit de clustering exploitable "
                "(silhouette non calculable) — repli sur epsilon du coude k-distance, résultat "
                "à interpréter avec prudence."
            )
    elif algorithme == "gmm":
        diagnostics = run_gmm_diagnostics(rfm_scaled, k_min=k_min, k_max=k_max)
        if not diagnostics:
            avertissements.append("Pas assez de clients pour exécuter le clustering GMM.")
            return RFMPipelineResult(
                n_clients=len(rfm_df),
                fenetre_jours=fenetre_jours,
                date_reference=date_ref,
                algorithme_utilise=algorithme,
                diagnostics=[],
                avertissements=avertissements,
            )
        k_choisi = choisir_k(diagnostics)
        silhouette_choisi = next((p.silhouette for p in diagnostics if p.k == k_choisi), None)
        modele_final = GaussianMixture(n_components=k_choisi, random_state=42, n_init=5)
        rfm_df["cluster_id"] = modele_final.fit_predict(rfm_scaled)
    else:
        diagnostics = run_kmeans_diagnostics(rfm_scaled, k_min=k_min, k_max=k_max)
        if not diagnostics:
            avertissements.append("Pas assez de clients pour exécuter le clustering K-Means.")
            return RFMPipelineResult(
                n_clients=len(rfm_df),
                fenetre_jours=fenetre_jours,
                date_reference=date_ref,
                algorithme_utilise=algorithme,
                diagnostics=[],
                avertissements=avertissements,
            )
        k_choisi = choisir_k(diagnostics)
        silhouette_choisi = next((p.silhouette for p in diagnostics if p.k == k_choisi), None)
        modele_final = KMeans(n_clusters=k_choisi, random_state=42, n_init=10)
        rfm_df["cluster_id"] = modele_final.fit_predict(rfm_scaled)

    rfm_df["segment_rfm"] = _assign_segments(rfm_df, fenetre_jours)

    # score_churn : heuristique récence-only, PAS un modèle entraîné (cf. docstring module).
    rfm_df["score_churn"] = ((rfm_df["score_r"].astype(float) - 1) / 4 * -1 + 1).round(4)
    # score_r=1 (le plus ancien) -> score_churn=1.0 ; score_r=5 (le plus récent) -> score_churn=0.0

    if not dry_run:
        maintenant = datetime.utcnow()
        for row in rfm_df.itertuples(index=False):
            client = db.get(Client, row.client_id)
            if client is None:
                continue
            client.recence_jours = int(row.recence_jours)
            client.frequence_achats = int(row.frequence_achats)
            client.montant_total = float(row.montant_total)
            client.score_r = int(row.score_r)
            client.score_f = int(row.score_f)
            client.score_m = int(row.score_m)
            client.segment_rfm = row.segment_rfm
            client.cluster_id = int(row.cluster_id)
            client.score_churn = float(row.score_churn)
            client.date_maj_segmentation = maintenant
        db.commit()

    return RFMPipelineResult(
        n_clients=len(rfm_df),
        fenetre_jours=fenetre_jours,
        date_reference=date_ref,
        algorithme_utilise=algorithme,
        diagnostics=diagnostics,
        k_choisi=k_choisi,
        eps_choisi=eps_choisi,
        silhouette_choisi=silhouette_choisi,
        ecrit_en_base=not dry_run,
        avertissements=avertissements,
    )
