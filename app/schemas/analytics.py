from datetime import date

from pydantic import BaseModel


class KPIsGlobaux(BaseModel):
    """Sortie de GET /api/v1/analytics/kpis — vue SQL v_kpis_globaux."""

    nb_clients: int
    nb_transactions: int
    chiffre_affaires_total: float
    stock_total_unites: int
    solde_tresorerie_estime: float


class PointPrevision(BaseModel):
    date_prevision: date
    valeur_prevue: float
    borne_basse: float | None = None
    borne_haute: float | None = None

class ImportanceVariable(BaseModel):
    variable: str
    importance: float

class PrevisionVentes(BaseModel):
    """Sortie de GET /api/v1/analytics/forecast/sales — module Maslaw."""

    modele_utilise: str  # "ARIMA" | "Prophet" | "XGBoost"
    horizon_jours: int
    points: list[PointPrevision]
    rmse_validation: float | None = None
    mae_validation: float | None = None
    avertissements: list[str] = []
    explicabilite: list[ImportanceVariable] = []


class AlerteStock(BaseModel):
    code_produit: str
    nom_produit: str | None
    quantite_disponible: int
    seuil_alerte: int
    niveau: str  # "critique" | "attention" | "ok"


class PrevisionTresorerie(BaseModel):
    """Sortie du calcul de prévision trésorerie — même contrat que
    PrevisionVentes (tâche #1 de la roadmap de clôture)."""

    modele_utilise: str
    horizon_jours: int
    points: list[PointPrevision]
    rmse_validation: float | None = None
    mae_validation: float | None = None
    avertissements: list[str] = []


class AlerteTresorerie(BaseModel):
    date_alerte: date
    solde_projete: float
    seuil_critique: float
    niveau: str  # "ok" | "vigilance" | "risque_deficit"
    message: str


class PrevisionTresorerieAvecAlerte(BaseModel):
    """Sortie de GET /api/v1/analytics/cashflow/forecast (tâche #2)."""

    prevision: PrevisionTresorerie
    alerte: AlerteTresorerie