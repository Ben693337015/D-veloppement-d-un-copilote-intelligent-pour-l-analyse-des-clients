from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DiagnosticPointOut(BaseModel):
    k: int
    inertie: float
    silhouette: float | None
    # Davies-Bouldin (plus bas = meilleur) et Calinski-Harabasz (plus haut =
    # meilleur) — cf. README §6 et `rfm_clustering_service._metriques_clustering`.
    davies_bouldin: float | None = None
    calinski_harabasz: float | None = None
    # DBSCAN uniquement (None pour K-Means/GMM) — cf. docstring
    # `rfm_clustering_service.run_dbscan_diagnostics` pour la sémantique.
    n_clusters_trouves: int | None = None
    n_bruit: int | None = None


class ComparaisonAlgorithmeOut(BaseModel):
    """Évaluation d'un algorithme en mode « auto » (cf. comparaison_segmentation)."""

    algorithme: str
    nom: str
    statut: str  # "ok" | "erreur"
    eligible: bool
    motif_exclusion: str | None = None
    rang: int | None = None  # 1 = recommandé
    rang_moyen: float | None = None
    k_choisi: int | None = None
    eps_choisi: float | None = None
    silhouette: float | None = None
    davies_bouldin: float | None = None
    calinski_harabasz: float | None = None
    n_groupes: int = 0
    part_bruit: float = 0.0
    plus_petit_groupe: float = 0.0
    diagnostics: list[DiagnosticPointOut] = []
    repartition_segments: dict[str, int] = {}
    interpretation: list[str] = []
    duree_s: float = 0.0


class RFMPipelineRequest(BaseModel):
    # "auto" (défaut depuis le 13/10/2026) : K-Means, GMM et DBSCAN sont
    # évalués et le meilleur est retenu ; ou un algorithme imposé.
    algorithme: str = "auto"  # "auto" | "kmeans" | "dbscan" | "gmm"
    k_min: int = 2
    k_max: int = 6
    fenetre_jours: int | None = None
    winsor_percentile: float | None = None
    dry_run: bool = False


class RFMPipelineResultOut(BaseModel):
    n_clients: int
    fenetre_jours: int
    date_reference: datetime | None
    algorithme_utilise: str
    diagnostics: list[DiagnosticPointOut]
    k_choisi: int | None
    eps_choisi: float | None = None
    silhouette_choisi: float | None
    davies_bouldin_choisi: float | None = None
    calinski_harabasz_choisi: float | None = None
    ecrit_en_base: bool
    avertissements: list[str]
    comparaison: list[ComparaisonAlgorithmeOut] = []
    synthese: list[str] = []
