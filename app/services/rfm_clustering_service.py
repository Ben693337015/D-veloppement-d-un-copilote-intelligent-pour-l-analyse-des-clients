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
     Ex aequo départagés par rang MOYEN : deux clients identiques ont
     toujours le même score.
  4. Winsorizing puis transformation log1p de F et M (distributions très
     asymétriques), puis standardisation z-score avant clustering (sinon le
     Montant écrase la Récence dans le calcul de distance).
  5. Diagnostic coude (inertie) + score de silhouette pour k = 2..6 (mêmes
     bornes que l'écran "Coude K-Means" fourni en exemple).
  6. Choix de k par silhouette MAXIMALE (pas uniquement le coude visuel) —
     cohérent avec le constat déjà documenté sur données réelles Online
     Retail II : k=2 gagne souvent face à k=3 malgré un coude visuel à 3,
     à cause d'un petit segment B2B/grossiste qui domine la variance.
  7. Étiquetage PAR CLIENT (cf. `_assign_segments`) : le cycle de vie
     (Nouveau / À risque / Perdu) vient d'une règle individuelle sur un
     seuil ABSOLU de récence ; le niveau de valeur (Fidèle / Occasionnel)
     des clients actifs récurrents combine le profil de leur cluster et
     leurs propres scores F/M. Les clients sans achat dans la fenêtre sont
     réécrits "Perdu" (ils gardaient auparavant leur ancien segment).
  8. `score_churn` : heuristique ABSOLUE = récence / fenêtre, bornée à [0, 1]
     (PAS un modèle de classification entraîné). À documenter comme tel dans le
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

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.preprocessing import winsoriser as _winsoriser_partage
from app.models import Client, HistoriqueSegmentation, Transaction

ALGORITHMES_DISPONIBLES = {"kmeans", "dbscan", "gmm"}
SILHOUETTE_ECHANTILLON = 10_000
SEGMENT_NOUVEAU = "Nouveau"
SEGMENT_FIDELE = "Fidèle"
SEGMENT_RISQUE = "À risque"
SEGMENT_OCCASIONNEL = "Occasionnel"
SEGMENT_ATYPIQUE = "Atypique (B2B/grossiste)"
# Client inactif depuis plus de la moitié de la fenêtre d'analyse ET qui n'a
# jamais été récurrent (un seul achat), OU qui n'a plus aucun achat dans la
# fenêtre d'analyse. Avant ce segment, un acheteur unique d'il y a 300 jours
# était étiqueté "Nouveau" — faux métier (cf. audit du 2026-10-09, bug n°4).
SEGMENT_PERDU = "Perdu"

# Seuils d'étiquetage PAR CLIENT (cf. `_assign_segments`).
SEUIL_SCORE_INDIVIDUEL = 3  # score F et M (1-5) minimum pour être "Fidèle"
SEUIL_SCORE_ATYPIQUE = 4  # score M minimum pour qu'un point isolé DBSCAN soit "Atypique"


@dataclass
class DiagnosticPoint:
    k: int
    inertie: float
    silhouette: float | None
    # Davies-Bouldin (plus BAS = meilleur) et Calinski-Harabasz (plus HAUT =
    # meilleur) — cf. README §6 : annoncés comme métriques complémentaires à
    # la silhouette mais jusqu'ici jamais calculés (gap documentation/code
    # corrigé ici). Même garde de calcul que la silhouette (`_metriques_clustering`) :
    # None si un seul cluster ou trop peu de points pour être significatif.
    davies_bouldin: float | None = None
    calinski_harabasz: float | None = None
    # Champs supplémentaires, utilisés seulement par DBSCAN — None pour
    # K-Means/GMM. Cf. docstring module pour la sémantique de réutilisation
    # de `k`/`inertie` selon l'algorithme (index de candidat/epsilon testé).
    n_clusters_trouves: int | None = None
    n_bruit: int | None = None


def _metriques_clustering(
    donnees: np.ndarray, labels: np.ndarray
) -> tuple[float | None, float | None, float | None]:
    """Calcule silhouette, Davies-Bouldin et Calinski-Harabasz pour un même
    partitionnement — factorisé pour être appelé identiquement par K-Means,
    GMM et DBSCAN (sur le sous-ensemble hors-bruit pour ce dernier), plutôt
    que dupliquer la même garde ("au moins 2 clusters, au moins un point de
    plus que de clusters") à trois endroits différents.

    Les trois métriques sont TOUJOURS calculées ensemble (jamais silhouette
    seule) : le README §6 annonce Davies-Bouldin et Calinski-Harabasz comme
    métriques complémentaires de sélection, elles doivent donc être
    réellement disponibles partout où la silhouette l'est, sous peine de
    documenter une mesure de performance qui n'existe pas dans le code
    (cf. Cadrage Tâche #10 : "les critères d'évaluation retenus" doivent
    être vérifiables, pas seulement mentionnés)."""
    n_clusters = len(set(labels))
    if n_clusters <= 1 or len(labels) <= n_clusters:
        return None, None, None
    # AJOUT (13/10/2026) : au-delà de SILHOUETTE_ECHANTILLON clients, la
    # silhouette (coût en n², 5 s par calcul sur 17 614 clients, 19 calculs
    # en mode « auto ») est estimée sur un échantillon FIXE (random_state)
    # — option prévue par scikit-learn. Écart mesuré ≈ 0,001, résultat
    # reproductible. Davies-Bouldin et Calinski-Harabasz (coût linéaire)
    # restent calculés sur tous les clients.
    echantillon = SILHOUETTE_ECHANTILLON if len(labels) > SILHOUETTE_ECHANTILLON else None
    return (
        float(silhouette_score(donnees, labels, sample_size=echantillon, random_state=42)),
        float(davies_bouldin_score(donnees, labels)),
        float(calinski_harabasz_score(donnees, labels)),
    )


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
    davies_bouldin_choisi: float | None = None
    calinski_harabasz_choisi: float | None = None
    ecrit_en_base: bool = False
    avertissements: list[str] = field(default_factory=list)
    # Mode « auto » : évaluation des 3 algorithmes (cf. comparaison_segmentation).
    comparaison: list[dict] = field(default_factory=list)
    synthese: list[str] = field(default_factory=list)


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
    Le rang en PERCENTILE (0-1) ramène toujours la variable sur une échelle
    1-5 comparable entre axes R/F/M, quel que soit le nombre de valeurs
    distinctes de la variable brute.

    BUG CORRIGÉ (audit 2026-10-09, bug n°3) : les égalités étaient départagées
    par `method="first"`, c'est-à-dire par l'ORDRE DE LECTURE en base — deux
    clients strictement identiques (ex. huit acheteurs à 1 seule facture)
    recevaient des scores différents, de 1 à 5, selon l'ordre arbitraire des
    lignes renvoyées par la base. `method="average"` donne à tous les ex aequo
    le même rang moyen, donc le même score : le résultat ne dépend plus que
    des données, jamais de l'ordre de stockage.
    """
    if serie.nunique() < 2:
        return pd.Series(3, index=serie.index)  # une seule valeur -> score neutre
    rangs_percentile = serie.rank(method="average", ascending=not inverse, pct=True)
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
        silhouette, davies_bouldin, calinski_harabasz = _metriques_clustering(rfm_scaled, labels)
        points.append(
            DiagnosticPoint(
                k=k,
                inertie=float(modele.inertia_),
                silhouette=silhouette,
                davies_bouldin=davies_bouldin,
                calinski_harabasz=calinski_harabasz,
            )
        )
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
        silhouette, davies_bouldin, calinski_harabasz = _metriques_clustering(rfm_scaled, labels)
        points.append(
            DiagnosticPoint(
                k=k,
                inertie=float(modele.bic(rfm_scaled)),
                silhouette=silhouette,
                davies_bouldin=davies_bouldin,
                calinski_harabasz=calinski_harabasz,
            )
        )
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
        # Métriques calculées sur le sous-ensemble hors-bruit uniquement —
        # le bruit (-1) n'est pas un cluster au sens de ces métriques, et
        # l'y inclure fausserait silhouette/Davies-Bouldin/Calinski-Harabasz
        # (même principe que le calcul de silhouette existant avant cet ajout).
        silhouette = davies_bouldin = calinski_harabasz = None
        if n_clusters > 1 and masque_non_bruit.sum() > n_clusters:
            silhouette, davies_bouldin, calinski_harabasz = _metriques_clustering(
                rfm_scaled[masque_non_bruit], labels[masque_non_bruit]
            )
        points.append(
            DiagnosticPoint(
                k=i,
                inertie=eps,
                silhouette=silhouette,
                davies_bouldin=davies_bouldin,
                calinski_harabasz=calinski_harabasz,
                n_clusters_trouves=n_clusters,
                n_bruit=n_bruit,
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
    """Étiquette chaque client — PAR CLIENT, plus par moyenne de cluster.

    BUG CORRIGÉ (audit 2026-10-09, bugs n°1 et n°2) : la version précédente
    attribuait l'étiquette au CLUSTER entier à partir de sa récence MOYENNE.
    Comme le choix par silhouette maximale retient souvent k=2 sur des
    données réelles, chaque cluster mélange clients actifs et dormants :
    la récence moyenne ne dépassait jamais le seuil de risque et le segment
    "À risque" n'apparaissait JAMAIS (vérifié sur 1 500 clients simulés :
    0 "À risque", des clients inactifs depuis 339 jours étiquetés "Fidèle").
    Or repérer les clients à risque est le besoin central de l'US-04.

    Nouvelle logique en deux dimensions, documentée pour le rapport :

    1. CYCLE DE VIE — règle individuelle, absolue (indépendante du clustering) :
       un client est "dormant" si sa récence dépasse la moitié de la fenêtre
       d'analyse (6 mois pour 12 mois, pratique RFM courante).
         - dormant + récurrent (≥ 2 achats)   -> "À risque"
         - dormant + un seul achat           -> "Perdu"
         - actif   + un seul achat           -> "Nouveau"
       Ces règles priment : un ancien très bon client devenu silencieux est
       exactement celui que la campagne de relance doit viser.

    2. NIVEAU DE VALEUR — pour les clients actifs et récurrents uniquement,
       c'est là que le clustering apporte son information : le cluster est
       "haute valeur" si ses membres actifs récurrents ont en moyenne des
       scores F et M ≥ 3. Le client est "Fidèle" s'il appartient à un tel
       cluster ET que ses PROPRES scores F et M sont ≥ 3 (garde-fou contre un
       client modeste rangé dans un cluster de bons clients) ; sinon
       "Occasionnel".

    DBSCAN — points de bruit (cluster_id == -1) : un point isolé n'est plus
    automatiquement "Atypique (B2B/grossiste)". Vérifié sur données
    réalistes : le bruit DBSCAN était surtout composé de clients DORMANTS
    (récence moyenne 239 j), pas de grossistes. Il n'est "Atypique" que s'il
    est actif ET à forte valeur (score M ≥ 4) — le profil grossiste visé.
    Un point isolé dormant suit la règle de cycle de vie (À risque / Perdu),
    les autres sont "Occasionnel" ou "Nouveau".
    """
    seuil_jours_risque = fenetre_jours / 2
    dormant = df["recence_jours"] > seuil_jours_risque
    recurrent = df["frequence_achats"] > 1
    bruit = df["cluster_id"] == -1

    # Profil de valeur de chaque cluster, calculé sur ses membres ACTIFS et
    # RÉCURRENTS (les acheteurs uniques et les dormants fausseraient la moyenne).
    actifs_recurrents = df[recurrent & ~dormant & ~bruit]
    stats = actifs_recurrents.groupby("cluster_id").agg(
        score_f_moyen=("score_f", "mean"), score_m_moyen=("score_m", "mean")
    )
    clusters_haute_valeur = set(
        stats.index[
            (stats["score_f_moyen"] >= SEUIL_SCORE_INDIVIDUEL)
            & (stats["score_m_moyen"] >= SEUIL_SCORE_INDIVIDUEL)
        ]
    )
    cluster_haute_valeur = df["cluster_id"].isin(clusters_haute_valeur)
    client_haute_valeur = (df["score_f"] >= SEUIL_SCORE_INDIVIDUEL) & (df["score_m"] >= SEUIL_SCORE_INDIVIDUEL)

    # np.select applique la PREMIÈRE condition vraie : l'ordre encode la priorité.
    conditions = [
        dormant & recurrent,
        dormant & ~recurrent,
        bruit & (df["score_m"] >= SEUIL_SCORE_ATYPIQUE),
        ~recurrent,
        ~bruit & cluster_haute_valeur & client_haute_valeur,
    ]
    choix = [SEGMENT_RISQUE, SEGMENT_PERDU, SEGMENT_ATYPIQUE, SEGMENT_NOUVEAU, SEGMENT_FIDELE]
    return pd.Series(np.select(conditions, choix, default=SEGMENT_OCCASIONNEL), index=df.index)


def calculer_score_churn(recence_jours: pd.Series, fenetre_jours: int) -> pd.Series:
    """Score de risque de perte (0 = acheté aujourd'hui, 1 = inactif depuis
    toute la fenêtre d'analyse ou plus).

    BUG CORRIGÉ (audit 2026-10-09, bug n°3) : l'ancien score était
    `1 - (score_r - 1) / 4`, donc un RANG RELATIF : 20 % des clients avaient
    toujours un churn de 1,0 même si tous avaient acheté la veille, et le
    score pouvait contredire le segment (un "Fidèle" avec churn = 1,0 a été
    observé). Le nouveau score est ABSOLU et aligné sur le seuil de
    `_assign_segments` : pour un client récurrent, churn > 0,5 ⇔ "À risque".

    Reste une HEURISTIQUE (pas un modèle entraîné) — à présenter comme telle
    dans le rapport ; un modèle probabiliste (BG/NBD, probabilité d'être
    encore actif) est prévu à l'étape suivante.
    """
    return (recence_jours.astype(float) / float(fenetre_jours)).clip(0.0, 1.0).round(4)


def _clients_hors_fenetre(db: Session, date_ref: datetime | None, ids_dans_fenetre: set) -> pd.DataFrame:
    """Clients ayant au moins un achat (hors retours) en base, mais aucun
    dans la fenêtre d'analyse — avec leur récence RÉELLE (jours depuis leur
    dernier achat, toutes dates confondues)."""
    colonnes = ["client_id", "recence_jours"]
    if date_ref is None:
        return pd.DataFrame(columns=colonnes)
    lignes = (
        db.query(Transaction.client_id, func.max(Transaction.date_transaction))
        .filter(~Transaction.est_retour, Transaction.client_id.is_not(None))
        .group_by(Transaction.client_id)
        .all()
    )
    hors = [
        (client_id, (date_ref - derniere).days)
        for client_id, derniere in lignes
        if client_id not in ids_dans_fenetre and derniere is not None
    ]
    return pd.DataFrame(hors, columns=colonnes)


ORDRE_ALGORITHMES = ("kmeans", "gmm", "dbscan")


def _clusteriser(
    algorithme: str, rfm_df: pd.DataFrame, rfm_scaled: np.ndarray, k_min: int, k_max: int
) -> dict:
    """Diagnostic + choix du paramètre (k ou epsilon) + ajustement final d'UN
    algorithme sur la matrice RFM déjà prétraitée. Factorisé (13/10/2026) pour
    que le mode « auto » évalue les 3 algorithmes sur exactement les mêmes
    données ; logique identique à l'ancienne version de run_full_pipeline.

    Retourne un dict : ok, labels, k, eps, silhouette, davies_bouldin,
    calinski_harabasz, diagnostics, avertissements, duree_s."""
    import time

    debut = time.perf_counter()
    res = {"algorithme": algorithme, "ok": False, "labels": None, "k": None, "eps": None,
           "silhouette": None, "davies_bouldin": None, "calinski_harabasz": None,
           "diagnostics": [], "avertissements": [], "duree_s": 0.0}
    noms = {"kmeans": "K-Means", "gmm": "GMM", "dbscan": "DBSCAN"}

    if algorithme == "dbscan":
        diagnostics, eps_repli = run_dbscan_diagnostics(rfm_scaled)
        if not diagnostics:
            res["avertissements"].append("Pas assez de clients pour exécuter DBSCAN.")
            return res
        eps_choisi = choisir_eps(diagnostics, eps_repli)
        candidat = min(diagnostics, key=lambda p: abs(p.inertie - eps_choisi))
        min_samples_effectif = min(5, max(2, len(rfm_df) - 1))
        labels = DBSCAN(eps=eps_choisi, min_samples=min_samples_effectif).fit_predict(rfm_scaled)
        res.update(eps=eps_choisi, silhouette=candidat.silhouette, davies_bouldin=candidat.davies_bouldin,
                   calinski_harabasz=candidat.calinski_harabasz)
        n_atypiques = int((labels == -1).sum())
        if n_atypiques:
            res["avertissements"].append(
                f"{n_atypiques} client(s) isolé(s) comme atypique(s) par DBSCAN (segment "
                f"'{SEGMENT_ATYPIQUE}') — probablement des profils B2B/grossistes que K-Means "
                "aurait forcés dans un cluster 'normal' (cf. README §6)."
            )
        if candidat.silhouette is None:
            res["avertissements"].append(
                "Aucune valeur d'epsilon testée n'a produit de clustering exploitable "
                "(silhouette non calculable) — repli sur epsilon du coude k-distance, résultat "
                "à interpréter avec prudence."
            )
    else:
        fonction = run_gmm_diagnostics if algorithme == "gmm" else run_kmeans_diagnostics
        diagnostics = fonction(rfm_scaled, k_min=k_min, k_max=k_max)
        if not diagnostics:
            res["avertissements"].append(f"Pas assez de clients pour exécuter le clustering {noms[algorithme]}.")
            return res
        k_choisi = choisir_k(diagnostics)
        point = next((p for p in diagnostics if p.k == k_choisi), None)
        modele = (
            GaussianMixture(n_components=k_choisi, random_state=42, n_init=5)
            if algorithme == "gmm"
            else KMeans(n_clusters=k_choisi, random_state=42, n_init=10)
        )
        labels = modele.fit_predict(rfm_scaled)
        res.update(k=k_choisi, silhouette=point.silhouette if point else None,
                   davies_bouldin=point.davies_bouldin if point else None,
                   calinski_harabasz=point.calinski_harabasz if point else None)

    res.update(ok=True, labels=labels, diagnostics=diagnostics, duree_s=time.perf_counter() - debut)
    return res


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
    if algorithme not in ALGORITHMES_DISPONIBLES | {"auto"}:
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

    # BUG CORRIGÉ (reproductibilité, test de bout en bout) : K-Means/GMM
    # dépendent de l'ORDRE des lignes (initialisation k-means++ par indices),
    # et cet ordre était celui, non garanti, renvoyé par PostgreSQL. Sur
    # AdventureWorks, la silhouette de k=6 variait de 0,4871 à 0,4896 selon
    # l'ordre (k=2 : 0,4894) : les MÊMES données donnaient k=2 ou k=6 d'un
    # import à l'autre. Tri déterministe sur les seules valeurs R/F/M : même
    # jeu de données -> même résultat, quel que soit l'ordre du fichier.
    rfm_df = rfm_df.sort_values(
        ["recence_jours", "frequence_achats", "montant_total"], kind="mergesort"
    ).reset_index(drop=True)

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
            f"{n_montant_plafonne} client(s) au montant plafonné (plafond = min(P{winsor_percentile:.0f}, "
            "Q3 + 1,5×IQR), cf. app.core.preprocessing.winsoriser) avant clustering (valeurs extrêmes neutralisées pour le K-Means uniquement — "
            "les montants réels stockés en base ne sont pas modifiés)."
        )
    if n_frequence_plafonnee:
        avertissements.append(
            f"{n_frequence_plafonnee} client(s) à la fréquence plafonnée (même plafond "
            f"min(P{winsor_percentile:.0f}, Q3 + 1,5×IQR)) avant clustering (même principe que pour le montant)."
        )

    # TRANSFORMATION LOGARITHMIQUE (log1p) de F et M avant standardisation :
    # leurs distributions sont très asymétriques (beaucoup de petits clients,
    # une longue traîne de gros). Sans elle, la distance euclidienne est
    # dominée par la traîne et le clustering sépare surtout "très gros" vs
    # "tous les autres". log1p (= log(1+x)) reste défini pour x = 0. La
    # récence, déjà bornée par la fenêtre d'analyse, n'est pas transformée.
    scaler = StandardScaler()
    rfm_scaled = scaler.fit_transform(
        pd.DataFrame(
            {
                "recence_jours": rfm_df["recence_jours"],
                "frequence_achats": np.log1p(frequence_clustering.astype(float)),
                "montant_total": np.log1p(montant_clustering.astype(float).clip(lower=0)),
            }
        )
    )

    comparaison: list[dict] = []
    synthese: list[str] = []
    if algorithme == "auto":
        # AJOUT (13/10/2026) : les 3 algorithmes sont évalués sur la MÊME
        # matrice RFM prétraitée, puis le meilleur est retenu selon des
        # critères explicites (cf. app/services/comparaison_segmentation.py).
        # Les 3 algorithmes tournent en parallèle (calculs numpy/scikit-learn,
        # qui libèrent le GIL) : durée ≈ celle du plus lent, pas leur somme.
        from concurrent.futures import ThreadPoolExecutor

        from app.services import comparaison_segmentation

        with ThreadPoolExecutor(max_workers=len(ORDRE_ALGORITHMES)) as pool:
            futurs = {a: pool.submit(_clusteriser, a, rfm_df, rfm_scaled, k_min, k_max) for a in ORDRE_ALGORITHMES}
            resultats = {a: f.result() for a, f in futurs.items()}
        comparaison, algorithme_recommande, synthese = comparaison_segmentation.comparer(
            resultats, rfm_df, fenetre_jours
        )
        if algorithme_recommande is None:
            avertissements += [t for r in resultats.values() for t in r["avertissements"]]
            return RFMPipelineResult(
                n_clients=len(rfm_df),
                fenetre_jours=fenetre_jours,
                date_reference=date_ref,
                algorithme_utilise="auto",
                avertissements=avertissements,
                comparaison=comparaison,
                synthese=synthese,
            )
        algorithme = algorithme_recommande
        retenu = resultats[algorithme]
    else:
        retenu = _clusteriser(algorithme, rfm_df, rfm_scaled, k_min, k_max)

    avertissements += retenu["avertissements"]
    if not retenu["ok"]:
        return RFMPipelineResult(
            n_clients=len(rfm_df),
            fenetre_jours=fenetre_jours,
            date_reference=date_ref,
            algorithme_utilise=algorithme,
            diagnostics=[],
            avertissements=avertissements,
        )
    diagnostics = retenu["diagnostics"]
    k_choisi = retenu["k"]
    eps_choisi = retenu["eps"]
    silhouette_choisi = retenu["silhouette"]
    davies_bouldin_choisi = retenu["davies_bouldin"]
    calinski_harabasz_choisi = retenu["calinski_harabasz"]
    rfm_df["cluster_id"] = retenu["labels"]

    rfm_df["segment_rfm"] = _assign_segments(rfm_df, fenetre_jours)

    # score_churn : heuristique ABSOLUE sur la récence (cf. `calculer_score_churn`).
    rfm_df["score_churn"] = calculer_score_churn(rfm_df["recence_jours"], fenetre_jours)

    hors_fenetre = _clients_hors_fenetre(db, date_ref, set(rfm_df["client_id"]))
    if not hors_fenetre.empty:
        avertissements.append(
            f"{len(hors_fenetre)} client(s) sans aucun achat depuis plus de {fenetre_jours} jours "
            f"(hors fenêtre d'analyse) : classé(s) '{SEGMENT_PERDU}', churn = 1."
        )

    if not dry_run:
        # OPTIMISATION (test de bout en bout) : l'écriture faisait un
        # `db.get(Client, id)` par client, soit 17 614 SELECT pour
        # AdventureWorks (30 s sur 45 s de pipeline). Mise à jour groupée
        # par clé primaire (UPDATE exécuté en lots), même résultat.
        maintenant = datetime.utcnow()
        lignes = [
            {
                "client_id": row.client_id,
                "recence_jours": int(row.recence_jours),
                "frequence_achats": int(row.frequence_achats),
                "montant_total": float(row.montant_total),
                "score_r": int(row.score_r),
                "score_f": int(row.score_f),
                "score_m": int(row.score_m),
                "segment_rfm": row.segment_rfm,
                "cluster_id": int(row.cluster_id),
                "score_churn": float(row.score_churn),
                "date_maj_segmentation": maintenant,
            }
            for row in rfm_df.itertuples(index=False)
        ]
        # BUG CORRIGÉ (audit 2026-10-09, bug n°4) : les clients sans achat dans
        # la fenêtre d'analyse n'étaient jamais réécrits et GARDAIENT leur
        # ancien segment (un client inactif depuis plus d'un an restait
        # "Fidèle"). Ce sont précisément les clients perdus : on le dit.
        lignes += [
            {
                "client_id": row.client_id,
                "recence_jours": int(row.recence_jours),
                "frequence_achats": 0,  # aucun achat DANS la fenêtre d'analyse
                "montant_total": 0.0,
                "score_r": 1,
                "score_f": 1,
                "score_m": 1,
                "segment_rfm": SEGMENT_PERDU,
                "cluster_id": None,  # pas inclus dans le clustering
                "score_churn": 1.0,
                "date_maj_segmentation": maintenant,
            }
            for row in hors_fenetre.itertuples(index=False)
        ]
        for i in range(0, len(lignes), 5000):
            db.execute(update(Client), lignes[i : i + 5000])
        # AJOUT (13/10/2026) : trace de l'exécution (métriques + diagnostic),
        # jusqu'ici perdue après affichage — lue par le copilote.
        repartition = rfm_df["segment_rfm"].value_counts().to_dict()
        if not hors_fenetre.empty:
            repartition[SEGMENT_PERDU] = repartition.get(SEGMENT_PERDU, 0) + len(hors_fenetre)
        db.add(
            HistoriqueSegmentation(
                date_execution=maintenant,
                algorithme=algorithme,
                n_clients=len(rfm_df),
                n_clients_hors_fenetre=len(hors_fenetre),
                fenetre_jours=fenetre_jours,
                date_reference=date_ref,
                k_choisi=k_choisi,
                eps_choisi=eps_choisi,
                silhouette=silhouette_choisi,
                davies_bouldin=davies_bouldin_choisi,
                calinski_harabasz=calinski_harabasz_choisi,
                repartition_segments=json.dumps({k: int(v) for k, v in repartition.items()}, ensure_ascii=False),
                diagnostics=json.dumps([asdict(d) for d in diagnostics], ensure_ascii=False, default=float),
                avertissements=json.dumps(avertissements, ensure_ascii=False),
                comparaison=json.dumps(comparaison, ensure_ascii=False, default=float) if comparaison else None,
                synthese=json.dumps(synthese, ensure_ascii=False) if synthese else None,
            )
        )
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
        davies_bouldin_choisi=davies_bouldin_choisi,
        calinski_harabasz_choisi=calinski_harabasz_choisi,
        ecrit_en_base=not dry_run,
        avertissements=avertissements,
        comparaison=comparaison,
        synthese=synthese,
    )
