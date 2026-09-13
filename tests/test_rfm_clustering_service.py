import numpy as np
import pandas as pd

from app.models import Client, Transaction
from app.services import rfm_clustering_service as svc


def _seed_three_profiles(db_session, rng_seed: int = 0):
    """Trois profils synthétiques bien séparés : fidèles (récent/fréquent/gros
    montants), à risque (achats anciens mais répétés), occasionnels/nouveaux
    (une seule commande récente)."""
    rng = np.random.default_rng(rng_seed)
    base_date = pd.Timestamp("2024-06-01")

    for i in range(30):
        c = Client(code_client_externe=f"FID-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(int(rng.integers(8, 15))):
            jour = base_date - pd.Timedelta(days=int(rng.integers(0, 30)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"F{i}-{j}",
                    quantite=int(rng.integers(1, 5)),
                    prix_unitaire=float(rng.uniform(50, 200)),
                    date_transaction=jour,
                    est_retour=False,
                )
            )

    for i in range(20):
        c = Client(code_client_externe=f"RISK-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(int(rng.integers(2, 5))):
            jour = base_date - pd.Timedelta(days=int(rng.integers(200, 350)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"R{i}-{j}",
                    quantite=int(rng.integers(1, 3)),
                    prix_unitaire=float(rng.uniform(10, 50)),
                    date_transaction=jour,
                    est_retour=False,
                )
            )

    for i in range(25):
        c = Client(code_client_externe=f"OCC-{i}")
        db_session.add(c)
        db_session.flush()
        jour = base_date - pd.Timedelta(days=int(rng.integers(0, 100)))
        db_session.add(
            Transaction(
                client_id=c.client_id,
                numero_facture=f"O{i}",
                quantite=1,
                prix_unitaire=float(rng.uniform(10, 30)),
                date_transaction=jour,
                est_retour=False,
            )
        )

    db_session.commit()


def test_pipeline_does_not_mislabel_active_majority_as_at_risk(db_session):
    """Verrouille le bug corrigé : une minorité de comptes B2B/grossistes à
    très gros montants ne doit jamais faire passer la MAJORITÉ de clients
    actifs (achats fréquents et récents, mais modestes) pour des clients
    "À risque" — reproduit le biais documenté du projet (k=2, petit segment
    B2B dominant la variance sur données réelles Online Retail II)."""
    rng = np.random.default_rng(7)
    base_date = pd.Timestamp("2024-06-01")

    for i in range(5):
        c = Client(code_client_externe=f"B2B-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(8):
            jour = base_date - pd.Timedelta(days=int(rng.integers(1, 20)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"B{i}-{j}",
                    quantite=50,
                    prix_unitaire=float(rng.uniform(500, 900)),
                    date_transaction=jour,
                )
            )

    for i in range(80):
        c = Client(code_client_externe=f"REG-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(6):
            jour = base_date - pd.Timedelta(days=int(rng.integers(1, 40)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"R{i}-{j}",
                    quantite=2,
                    prix_unitaire=float(rng.uniform(10, 40)),
                    date_transaction=jour,
                )
            )
    db_session.commit()

    resultat = svc.run_full_pipeline(db_session, k_min=2, k_max=6)
    assert resultat.n_clients == 85

    clients = db_session.query(Client).all()
    for c in clients:
        prefixe = c.code_client_externe.split("-")[0]
        if prefixe == "B2B":
            assert c.segment_rfm == "Fidèle", c.code_client_externe
        elif prefixe == "REG":
            # Le point clé du test : jamais "À risque" pour des clients qui
            # achètent activement (récence moyenne de quelques jours).
            assert c.segment_rfm != "À risque", c.code_client_externe


def test_pipeline_winsorizes_single_extreme_client_without_wrecking_others(db_session):
    """PRÉTRAITEMENT (winsorizing) : un seul client avec un montant aberrant
    (faute de frappe sur un prix unitaire) ne doit plus forcer K-Means à
    fusionner TOUS les autres clients dans un cluster indifférencié —
    verrouille le bug corrigé (avant prétraitement : les 50 clients normaux,
    pourtant très divers, étaient tous étiquetés identiquement)."""
    rng = np.random.default_rng(11)
    base_date = pd.Timestamp("2024-06-01")

    for i in range(50):
        c = Client(code_client_externe=f"NORM-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(int(rng.integers(2, 9))):
            jour = base_date - pd.Timedelta(days=int(rng.integers(1, 90)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"N{i}-{j}",
                    quantite=int(rng.integers(1, 3)),
                    prix_unitaire=float(rng.uniform(15, 60)),
                    date_transaction=jour,
                )
            )

    c_erreur = Client(code_client_externe="ERREUR-SAISIE")
    db_session.add(c_erreur)
    db_session.flush()
    db_session.add(
        Transaction(
            client_id=c_erreur.client_id,
            numero_facture="E1-1",
            quantite=2,
            prix_unitaire=50000.0,  # faute de frappe probable
            date_transaction=base_date - pd.Timedelta(days=5),
        )
    )
    db_session.add(
        Transaction(
            client_id=c_erreur.client_id,
            numero_facture="E1-2",
            quantite=1,
            prix_unitaire=45.0,
            date_transaction=base_date - pd.Timedelta(days=10),
        )
    )
    db_session.commit()

    resultat = svc.run_full_pipeline(db_session, k_min=2, k_max=6)
    assert any("plafonné" in a for a in resultat.avertissements)

    segments_normaux = {
        c.segment_rfm
        for c in db_session.query(Client).filter(Client.code_client_externe.like("NORM-%")).all()
    }
    # Les 50 clients normaux doivent être répartis sur PLUSIEURS segments
    # (leur diversité réelle de récence/fréquence doit rester détectable),
    # pas tous écrasés dans un seul et même segment par l'outlier.
    assert len(segments_normaux) > 1

    # Le montant RÉEL stocké en base ne doit jamais être altéré par le
    # plafonnement (qui ne s'applique qu'au calcul interne du clustering).
    erreur = db_session.query(Client).filter_by(code_client_externe="ERREUR-SAISIE").first()
    assert erreur.montant_total == 100045.00


def test_pipeline_separates_three_distinct_profiles(db_session):
    _seed_three_profiles(db_session)

    resultat = svc.run_full_pipeline(db_session, k_min=2, k_max=6)

    assert resultat.n_clients == 75
    assert resultat.k_choisi == 3
    assert resultat.silhouette_choisi is not None and resultat.silhouette_choisi > 0.5
    assert len(resultat.diagnostics) == 5  # k=2..6
    assert resultat.ecrit_en_base is True

    clients = db_session.query(Client).all()
    # Chaque profil synthétique doit être étiqueté correctement — verrouille
    # le bug déjà rencontré où "À risque" pouvait être écrasé par "Nouveau"
    # quand le cluster à faible score composite coïncidait avec les
    # acheteurs ponctuels plutôt qu'avec les clients réellement dormants.
    for c in clients:
        prefixe = c.code_client_externe.split("-")[0]
        if prefixe == "FID":
            assert c.segment_rfm == "Fidèle", c.code_client_externe
        elif prefixe == "RISK":
            assert c.segment_rfm == "À risque", c.code_client_externe
        elif prefixe == "OCC":
            assert c.segment_rfm == "Nouveau", c.code_client_externe

    # Toutes les colonnes RFM doivent être renseignées après le pipeline.
    assert all(c.score_churn is not None for c in clients)
    assert all(c.date_maj_segmentation is not None for c in clients)


def test_dry_run_does_not_write_to_database(db_session):
    _seed_three_profiles(db_session)

    resultat = svc.run_full_pipeline(db_session, dry_run=True)

    assert resultat.ecrit_en_base is False
    assert resultat.k_choisi is not None
    # Aucun client ne doit avoir été modifié en base.
    clients = db_session.query(Client).all()
    assert all(c.segment_rfm is None for c in clients)


def test_pipeline_handles_empty_database_gracefully(db_session):
    resultat = svc.run_full_pipeline(db_session)

    assert resultat.n_clients == 0
    assert resultat.k_choisi is None
    assert resultat.diagnostics == []
    assert len(resultat.avertissements) == 1


def test_score_quintile_handles_low_variance_data():
    """Cf. skill pme-marketing-rfm : qcut brut échoue silencieusement sur
    des données à faible variance/doublons. La version par rang doit
    toujours retourner un score exploitable.
    """
    serie_constante = pd.Series([100.0] * 10)
    scores = svc._score_quintile(serie_constante)
    assert (scores == 3).all()

    serie_deux_valeurs = pd.Series([1, 1, 1, 1, 1, 100])
    scores = svc._score_quintile(serie_deux_valeurs)
    assert scores.notna().all()


# ---------------------------------------------------------------------------
# DBSCAN / GMM (Phase 4) — cf. README §6 pour la justification méthodologique.
# ---------------------------------------------------------------------------


def _seed_b2b_and_regular_clients(db_session, rng_seed: int = 7, n_b2b: int = 3, n_reguliers: int = 80):
    """Même scénario que `test_pipeline_does_not_mislabel_active_majority_as_at_risk`
    (petite minorité B2B à très gros montants + majorité de clients réguliers
    modestes) — c'est PRÉCISÉMENT le cas d'usage que DBSCAN doit mieux gérer
    que K-Means. `n_b2b` volontairement < `min_samples` (5, cf.
    `run_full_pipeline`) : un groupe plus petit que `min_samples` ne peut
    structurellement PAS former son propre cluster DBSCAN valide (aucun de
    ses points ne peut devenir "core point"), il est donc classé "bruit" —
    exactement le comportement recherché. Avec n_b2b >= min_samples, un
    groupe suffisamment cohésif peut au contraire former son propre cluster
    dense (résultat légitime aussi : DBSCAN sépare alors B2B des clients
    réguliers SANS contamination croisée, sans qu'aucun k n'ait eu besoin
    d'être choisi à l'avance — vérifié manuellement, non testé ici pour ne
    pas dépendre d'un comportement sensible au seed)."""
    rng = np.random.default_rng(rng_seed)
    base_date = pd.Timestamp("2024-06-01")

    for i in range(n_b2b):
        c = Client(code_client_externe=f"B2B-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(8):
            jour = base_date - pd.Timedelta(days=int(rng.integers(1, 20)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"B{i}-{j}",
                    quantite=50,
                    prix_unitaire=float(rng.uniform(500, 900)),
                    date_transaction=jour,
                )
            )

    for i in range(n_reguliers):
        c = Client(code_client_externe=f"REG-{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(6):
            jour = base_date - pd.Timedelta(days=int(rng.integers(1, 40)))
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"R{i}-{j}",
                    quantite=2,
                    prix_unitaire=float(rng.uniform(10, 40)),
                    date_transaction=jour,
                )
            )
    db_session.commit()


def test_unknown_algorithme_raises_value_error(db_session):
    _seed_three_profiles(db_session)
    try:
        svc.run_full_pipeline(db_session, algorithme="reseau-de-neurones")
        raise AssertionError("Devrait avoir levé ValueError pour un algorithme inconnu")
    except ValueError as exc:
        assert "inconnu" in str(exc).lower()


def test_dbscan_isolates_b2b_clients_as_atypical_rather_than_kmeans_cluster(db_session):
    """Le test central du chantier DBSCAN : sur le scénario B2B + réguliers
    (celui-là même qui documente le biais K-Means dans le module), DBSCAN
    doit isoler les comptes B2B comme 'Atypique' plutôt que les regrouper
    dans un cluster classique — vérifie la valeur ajoutée réelle de
    l'algorithme, pas seulement qu'il tourne sans erreur."""
    _seed_b2b_and_regular_clients(db_session)

    resultat = svc.run_full_pipeline(db_session, algorithme="dbscan")

    assert resultat.algorithme_utilise == "dbscan"
    assert resultat.n_clients == 83
    assert resultat.k_choisi is None  # DBSCAN n'a pas de "k"
    assert resultat.eps_choisi is not None
    assert len(resultat.diagnostics) > 0
    # Le diagnostic DBSCAN peuple n_clusters_trouves/n_bruit (K-Means non).
    assert any(p.n_clusters_trouves is not None for p in resultat.diagnostics)

    clients = db_session.query(Client).all()
    b2b = [c for c in clients if c.code_client_externe.startswith("B2B-")]
    reguliers = [c for c in clients if c.code_client_externe.startswith("REG-")]
    # Au moins une partie des comptes B2B (profil extrême, isolé du reste
    # par construction) doit être détectée comme "Atypique" par DBSCAN.
    assert any(c.segment_rfm == "Atypique (B2B/grossiste)" for c in b2b)
    # La majorité des clients réguliers, eux, NE doivent PAS être noyés
    # dans le segment Atypique (sinon DBSCAN n'apporte rien face à K-Means).
    n_reguliers_atypiques = sum(1 for c in reguliers if c.segment_rfm == "Atypique (B2B/grossiste)")
    assert n_reguliers_atypiques < len(reguliers) / 2


def test_dbscan_dry_run_does_not_write_to_database(db_session):
    _seed_b2b_and_regular_clients(db_session)
    resultat = svc.run_full_pipeline(db_session, algorithme="dbscan", dry_run=True)
    assert resultat.ecrit_en_base is False
    clients = db_session.query(Client).all()
    assert all(c.segment_rfm is None for c in clients)


def test_dbscan_handles_empty_database_gracefully(db_session):
    resultat = svc.run_full_pipeline(db_session, algorithme="dbscan")
    assert resultat.n_clients == 0
    assert resultat.algorithme_utilise == "dbscan"


def test_dbscan_kdistance_excludes_each_point_from_its_own_neighbors():
    """BUG CORRIGÉ : `NearestNeighbors.kneighbors(X)` avec X explicite
    (plutôt que `.kneighbors()` sans argument) NE exclut PAS un point de
    ses propres voisins — chaque point se retrouvait son propre plus proche
    voisin (distance 0), décalant tout le calcul de la courbe k-distance
    d'un cran (distance au (min_samples-1)-ième voisin réel au lieu du
    min_samples-ième). Verrouille que ce n'est plus le cas : sur un jeu de
    points strictement distincts, aucune distance k-distance ne doit être
    nulle."""
    rng = np.random.default_rng(0)
    points = rng.normal(size=(30, 3))  # aucun doublon exact possible (continu)

    diagnostics, eps_coude = svc.run_dbscan_diagnostics(points, min_samples=5)

    assert eps_coude is not None
    assert eps_coude > 0
    assert all(p.inertie > 0 for p in diagnostics)


def test_detecter_coude_finds_the_bend_in_a_synthetic_curve():
    """Le détecteur de coude doit repérer le point d'inflexion sur une
    courbe synthétique construite pour avoir un coude net et connu à
    l'avance — vérifie la méthode géométrique indépendamment de DBSCAN."""
    plateau_bas = np.full(10, 1.0)
    montee = np.linspace(1.0, 50.0, 5)
    plateau_haut = np.full(10, 50.0)
    courbe = np.concatenate([plateau_bas, montee, plateau_haut])
    indice = svc._detecter_coude(courbe)
    # Le coude doit se situer dans la zone de montée, pas dans les plateaux.
    assert 9 <= indice <= 20


def test_gmm_produces_soft_clustering_alternative_to_kmeans(db_session):
    _seed_three_profiles(db_session)

    resultat = svc.run_full_pipeline(db_session, algorithme="gmm", k_min=2, k_max=6)

    assert resultat.algorithme_utilise == "gmm"
    assert resultat.k_choisi is not None
    assert resultat.eps_choisi is None  # pas d'epsilon pour GMM
    assert resultat.silhouette_choisi is not None
    # BIC (stocké dans `inertie`) doit être un nombre fini pour chaque k testé.
    assert all(np.isfinite(p.inertie) for p in resultat.diagnostics)

    clients = db_session.query(Client).all()
    for c in clients:
        prefixe = c.code_client_externe.split("-")[0]
        if prefixe == "FID":
            assert c.segment_rfm == "Fidèle", c.code_client_externe
        elif prefixe == "OCC":
            assert c.segment_rfm == "Nouveau", c.code_client_externe


def test_gmm_dry_run_does_not_write_to_database(db_session):
    _seed_three_profiles(db_session)
    resultat = svc.run_full_pipeline(db_session, algorithme="gmm", dry_run=True)
    assert resultat.ecrit_en_base is False
    clients = db_session.query(Client).all()
    assert all(c.segment_rfm is None for c in clients)


def test_rfm_run_endpoint_accepts_algorithme_param(client, db_session):
    _seed_b2b_and_regular_clients(db_session, n_reguliers=30)
    resp = client.post("/api/v1/marketing/rfm/run", json={"algorithme": "dbscan", "dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["algorithme_utilise"] == "dbscan"


def test_rfm_run_endpoint_rejects_unknown_algorithme(client, db_session):
    _seed_three_profiles(db_session)
    resp = client.post("/api/v1/marketing/rfm/run", json={"algorithme": "reseau-de-neurones"})
    assert resp.status_code == 422
