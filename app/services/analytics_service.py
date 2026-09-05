"""Service métier — sous-projet Maslaw (ventes, trésorerie, BFR, stocks).

La prévision des ventes est entraînée EN LIGNE (à la demande, sur le CA
journalier réel) par `app/services/forecasting_service.py` — cf. ce module
pour la méthodologie (Prophet/ARIMA/XGBoost, découpage train/test, RMSE/MAE).

Choix de modèle documenté dans README.md §"Choix des modèles & benchmarks" :
- ARIMA : référence quand la série est courte et stable (peu d'historique PME).
- Prophet : rapide à mettre en place, robuste à la saisonnalité/jours fériés.
- XGBoost : régression tabulaire sur variables exogènes calendaires (jour de
  semaine, mois, week-end, jour férié camerounais) + variables
  autorégressives (lag J-1, lag J-7, moyenne mobile 7j) — implémenté dans
  `forecasting_service._previsions_xgboost`. Exige plus d'historique que
  Prophet/ARIMA (14 jours mini, contre 7) à cause du lag J-7, et ne fournit
  pas d'intervalle de confiance natif.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Client, StockProduit, Transaction, Tresorerie


def get_kpis_globaux(db: Session) -> dict:
    """Calculé via l'ORM (portable Postgres/SQLite/tests) plutôt que la vue SQL
    `v_kpis_globaux` (celle-ci reste disponible dans scripts/init_db.sql pour un
    accès SQL direct / outils BI, mais l'API ne doit pas dépendre d'un moteur
    de BDD particulier)."""
    nb_clients = db.query(func.count(Client.client_id)).scalar() or 0
    nb_transactions = (
        db.query(func.count(Transaction.transaction_id))
        .filter(~Transaction.est_retour)
        .scalar()
        or 0
    )
    chiffre_affaires_total = (
        db.query(func.sum(Transaction.quantite * Transaction.prix_unitaire))
        .filter(~Transaction.est_retour)
        .scalar()
        or 0.0
    )
    stock_total_unites = db.query(func.sum(StockProduit.quantite_disponible)).scalar() or 0

    encaissements = (
        db.query(func.sum(Tresorerie.montant))
        .filter(Tresorerie.type_mouvement == "encaissement")
        .scalar()
        or 0.0
    )
    decaissements = (
        db.query(func.sum(Tresorerie.montant))
        .filter(Tresorerie.type_mouvement == "decaissement")
        .scalar()
        or 0.0
    )

    return {
        "nb_clients": nb_clients,
        "nb_transactions": nb_transactions,
        "chiffre_affaires_total": float(chiffre_affaires_total),
        "stock_total_unites": stock_total_unites,
        "solde_tresorerie_estime": float(encaissements) - float(decaissements),
    }


def get_stock_alertes(db: Session) -> list[dict]:
    """Retourne les produits sous ou proches du seuil de réapprovisionnement."""
    produits = db.query(StockProduit).all()
    alertes = []
    for p in produits:
        if p.quantite_disponible <= p.seuil_alerte:
            niveau = "critique"
        elif p.quantite_disponible <= p.seuil_reapprovisionnement:
            niveau = "attention"
        else:
            niveau = "ok"
        if niveau != "ok":
            alertes.append(
                {
                    "code_produit": p.code_produit,
                    "nom_produit": p.nom_produit,
                    "quantite_disponible": p.quantite_disponible,
                    "seuil_alerte": p.seuil_alerte,
                    "niveau": niveau,
                }
            )
    return alertes
