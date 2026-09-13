"""Génération simulée des tables stocks_produits et tresorerie.

Ces deux tables n'ont pas d'équivalent direct dans le dataset Online Retail II
(cf. load_online_retail.py) : elles sont donc simulées avec Faker/NumPy, mais
ANCRÉES sur les volumes de ventes réels déjà importés en base, afin de rester
cohérentes avec les transactions (cf. ROADMAP.md — "jeu de données ... réel,
simulé ou nettoyé" et Cadrage §3.1 Module A).

Bibliothèque de simulation : Faker (https://faker.readthedocs.io/) pour les
métadonnées produit, NumPy pour les distributions de quantités et de délais.

Usage :
    python -m app.ingestion.simulate_tresorerie_stocks
"""

import random

import numpy as np
import pandas as pd
from faker import Faker
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import StockProduit, Transaction, Tresorerie

fake = Faker("fr_FR")
rng = np.random.default_rng(seed=42)


def simulate_stocks(db: Session) -> int:
    """Un enregistrement de stock par code_produit distinct déjà vu dans transactions."""
    codes_produits = (
        db.query(Transaction.code_produit)
        .filter(Transaction.code_produit.is_not(None))
        .distinct()
        .limit(500)  # borne raisonnable pour un prototype PME
        .all()
    )

    count = 0
    for (code_produit,) in codes_produits:
        exists = db.query(StockProduit).filter_by(code_produit=code_produit).first()
        if exists:
            continue

        quantite_vendue_moy = (
            db.query(func.avg(Transaction.quantite))
            .filter(Transaction.code_produit == code_produit, ~Transaction.est_retour)
            .scalar()
            or 5
        )
        seuil_alerte = max(5, int(quantite_vendue_moy * 2))
        stock = StockProduit(
            code_produit=code_produit,
            nom_produit=fake.word().capitalize() + " " + fake.word(),
            quantite_disponible=int(rng.integers(0, seuil_alerte * 4)),
            seuil_alerte=seuil_alerte,
            seuil_reapprovisionnement=int(seuil_alerte * 1.5),
            delai_livraison_jours=int(rng.integers(3, 21)),
            cout_unitaire=round(float(rng.uniform(1, 50)), 2),
        )
        db.add(stock)
        count += 1

    db.commit()
    return count


def simulate_tresorerie(db: Session, jours_historique: int = 180) -> int:
    """Génère des mouvements de trésorerie journaliers ancrés sur le CA réel/jour.

    CORRECTION (bug connu, cf. mémoire du projet) : la version précédente
    groupait par `func.date(Transaction.date_transaction)` directement en
    SQL. Or `func.date()` produit un résultat *string* sous SQLite mais un
    objet `date` sous PostgreSQL — inconsistant entre tests et production.
    Une tentative de correction via `cast(..., Date)` s'est révélée PIRE :
    sous SQLite, `CAST(x AS DATE)` a une affinité NUMERIC et tronque
    silencieusement un horodatage à l'année seule (ex. "2010-12-01 08:26:00"
    -> entier 2010), ce qui aurait fusionné toutes les transactions d'une
    même année sur un seul "jour" fantôme.
    Solution robuste : ne JAMAIS déléguer la troncature de date à une
    fonction SQL dépendante du moteur. On récupère les lignes brutes via
    l'ORM (portable par construction) et on agrège par jour calendaire côté
    Python avec pandas, dont le comportement est identique quel que soit le
    SGBD utilisé.
    """
    lignes = (
        db.query(
            Transaction.date_transaction,
            Transaction.quantite,
            Transaction.prix_unitaire,
        )
        .filter(~Transaction.est_retour)
        .all()
    )

    if not lignes:
        ca_par_jour: list[tuple] = []
    else:
        df = pd.DataFrame(lignes, columns=["date_transaction", "quantite", "prix_unitaire"])
        df["jour"] = pd.to_datetime(df["date_transaction"]).dt.date
        df["ca"] = df["quantite"].astype(float) * df["prix_unitaire"].astype(float)
        agg = (
            df.groupby("jour", as_index=False)["ca"]
            .sum()
            .sort_values("jour")
            .head(jours_historique)
        )
        ca_par_jour = list(agg.itertuples(index=False, name=None))

    solde = 0.0
    count = 0
    categories_decaissement = ["fournisseur", "salaire", "impot", "loyer"]

    for jour, ca in ca_par_jour:
        encaissement = float(ca or 0)
        solde += encaissement
        db.add(
            Tresorerie(
                date_mouvement=jour,
                type_mouvement="encaissement",
                categorie="vente",
                montant=encaissement,
                solde_apres_mouvement=solde,
                commentaire="Encaissement simulé ancré sur le CA journalier réel",
            )
        )
        count += 1

        # 0 à 2 décaissements simulés par jour
        for _ in range(rng.integers(0, 3)):
            montant = round(float(rng.uniform(20, encaissement * 0.4 + 50)), 2)
            solde -= montant
            db.add(
                Tresorerie(
                    date_mouvement=jour,
                    type_mouvement="decaissement",
                    categorie=random.choice(categories_decaissement),
                    montant=montant,
                    solde_apres_mouvement=solde,
                    commentaire="Décaissement simulé (Faker/NumPy)",
                )
            )
            count += 1

    db.commit()
    return count


if __name__ == "__main__":
    session = SessionLocal()
    try:
        n_stocks = simulate_stocks(session)
        n_mouvements = simulate_tresorerie(session)
        print(f"Simulation terminée : {n_stocks} produits en stock, {n_mouvements} mouvements de trésorerie.")
    finally:
        session.close()
