from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DiagnosticPointOut(BaseModel):
    k: int
    inertie: float
    silhouette: float | None
    # DBSCAN uniquement (None pour K-Means/GMM) — cf. docstring
    # `rfm_clustering_service.run_dbscan_diagnostics` pour la sémantique.
    n_clusters_trouves: int | None = None
    n_bruit: int | None = None


class RFMPipelineRequest(BaseModel):
    algorithme: str = "kmeans"  # "kmeans" | "dbscan" | "gmm"
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
    ecrit_en_base: bool
    avertissements: list[str]
