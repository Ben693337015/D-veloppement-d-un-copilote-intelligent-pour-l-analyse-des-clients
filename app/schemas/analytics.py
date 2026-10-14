from datetime import date, datetime

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


class PrevisionVentes(BaseModel):
    """Sortie de GET /api/v1/analytics/forecast/sales — module Maslaw."""

    modele_utilise: str  # "ARIMA" | "Prophet" | "XGBoost"
    horizon_jours: int
    points: list[PointPrevision]
    rmse_validation: float | None = None
    mae_validation: float | None = None
    avertissements: list[str] = []


class PointHistorique(BaseModel):
    date: date
    valeur: float


class IndicateursPrevision(BaseModel):
    total_prevu: float | None = None
    moyenne_jour: float | None = None
    ecart_niveau: float | None = None  # vs moyenne des 30 derniers jours réels
    tendance: float | None = None  # dernière vs première semaine prévue
    sens: str | None = None  # "hausse" | "baisse" | "stable"
    erreur_relative: float | None = None  # RMSE / CA journalier récent
    fiabilite: str | None = None  # "bonne" | "moyenne" | "faible"


class PrevisionModele(PrevisionVentes):
    modele: str  # "prophet" | "arima" | "xgboost"
    nom: str
    statut: str  # "ok" | "erreur"
    erreur: str | None = None
    interpretation: list[str] = []
    indicateurs: IndicateursPrevision = IndicateursPrevision()


class SyntheseComparaison(BaseModel):
    meilleur_modele: str | None = None
    texte: list[str] = []


class LectureDirigeant(BaseModel):
    """Prévision traduite en langage métier, sans nom de modèle ni métrique
    technique (vue par défaut de l'interface, destinée aux dirigeants et
    employés de PME) — cf. `comparaison_previsions._lecture_dirigeant`."""

    disponible: bool
    titre: str
    resume: str
    total_prevu: float | None = None
    moyenne_jour: float | None = None
    evolution: float | None = None  # vs rythme des 30 derniers jours (fraction)
    sens_evolution: str | None = None  # "hausse" | "baisse" | "stable"
    evolution_texte: str | None = None
    fiabilite: str | None = None  # "bonne" | "moyenne" | "faible"
    fiabilite_texte: str | None = None
    ecart_jour: float | None = None  # écart moyen constaté par jour (MAE de validation)
    concordance: str | None = None  # "solide" | "incertaine"
    scenario_bas: float | None = None
    scenario_haut: float | None = None
    conseils: list[str] = []
    modele_retenu: str | None = None  # affiché en mode expert uniquement


class ComparaisonPrevisionsVentes(BaseModel):
    """Sortie de GET /api/v1/analytics/forecast/sales/comparaison."""

    horizon_jours: int
    historique: list[PointHistorique]
    modeles: list[PrevisionModele]
    synthese: SyntheseComparaison
    lecture_dirigeant: LectureDirigeant | None = None
    calcule_le: datetime
    depuis_cache: bool = False


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