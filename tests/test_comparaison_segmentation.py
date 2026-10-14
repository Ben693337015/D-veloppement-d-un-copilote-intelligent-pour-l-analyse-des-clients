"""Mode « auto » du pipeline RFM : évaluation de K-Means, GMM et DBSCAN."""

import numpy as np
import pandas as pd

from app.models import Client, HistoriqueSegmentation, Transaction
from app.services import comparaison_segmentation as cs
from app.services.rfm_clustering_service import DiagnosticPoint


def _seed(db, n_clients=60):
    """3 profils nets (petits, moyens, gros acheteurs) + dormants."""
    base = pd.Timestamp("2024-06-01")
    rng = np.random.default_rng(0)
    for i in range(n_clients):
        c = Client(code_client_externe=f"C{i}")
        db.add(c)
        db.flush()
        profil = i % 3
        n_achats = [1, 3, 8][profil]
        for j in range(n_achats):
            db.add(Transaction(client_id=c.client_id, numero_facture=f"F{i}-{j}", quantite=1,
                               prix_unitaire=[20, 150, 900][profil] * (1 + rng.random() * 0.2),
                               date_transaction=base - pd.Timedelta(days=int([250, 60, 5][profil] + j * 7 + i % 5))))
    db.commit()


def test_mode_auto_evalue_les_3_et_enregistre_le_meilleur(client, db_session):
    _seed(db_session)
    corps = client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 5}).json()

    algos = {a["algorithme"]: a for a in corps["comparaison"]}
    assert set(algos) == {"kmeans", "gmm", "dbscan"}
    recommande = next(a for a in corps["comparaison"] if a["rang"] == 1)
    assert corps["algorithme_utilise"] == recommande["algorithme"]
    assert corps["synthese"][0].startswith(f"Algorithme recommandé : {recommande['nom']}")
    for a in corps["comparaison"]:
        if a["statut"] == "ok":
            assert a["interpretation"] and a["diagnostics"]
            assert sum(a["repartition_segments"].values()) == corps["n_clients"]
    # les segments écrits et l'historique correspondent à l'algorithme retenu
    assert db_session.query(Client).filter(Client.segment_rfm.is_not(None)).count() == 60
    h = db_session.query(HistoriqueSegmentation).one()
    assert h.algorithme == recommande["algorithme"]


def test_algorithme_impose_reste_possible(client, db_session):
    _seed(db_session)
    corps = client.post("/api/v1/marketing/rfm/run", json={"algorithme": "gmm", "k_min": 2, "k_max": 3}).json()
    assert corps["algorithme_utilise"] == "gmm" and corps["comparaison"] == []


def test_dry_run_auto_n_ecrit_rien(client, db_session):
    _seed(db_session)
    corps = client.post("/api/v1/marketing/rfm/run", json={"dry_run": True}).json()
    assert corps["comparaison"] and corps["ecrit_en_base"] is False
    assert db_session.query(Client).filter(Client.segment_rfm.is_not(None)).count() == 0


# ── Règles d'éligibilité et de classement ──────────────────────────────────


def test_eligibilite():
    assert cs._eligibilite(15, 0.01, 0.3).startswith("15 groupes")
    assert "non classés" in cs._eligibilite(4, 0.25, 0.3)
    assert "silhouette" in cs._eligibilite(3, 0.0, None)
    assert cs._eligibilite(1, 0.0, 0.3).startswith("1 groupe")
    assert cs._eligibilite(5, 0.05, 0.3) is None


def test_echelle_de_kaufman_rousseeuw():
    assert [cs.qualite_silhouette(s) for s in (0.8, 0.6, 0.4, 0.1, None)] == [
        "forte", "raisonnable", "faible", "non substantielle", "non mesurable"]


def _res(algo, labels, sil, db, ch, k=None, eps=None):
    diag = [DiagnosticPoint(k=2, inertie=10.0, silhouette=None if sil is None else sil - 0.05),
            DiagnosticPoint(k=3, inertie=5.0, silhouette=sil)]
    return {"algorithme": algo, "ok": True, "labels": np.array(labels), "k": k, "eps": eps,
            "silhouette": sil, "davies_bouldin": db, "calinski_harabasz": ch,
            "diagnostics": diag, "avertissements": [], "duree_s": 0.1}


def _rfm(n):
    return pd.DataFrame({"client_id": range(n), "recence_jours": [10] * n, "frequence_achats": [3] * n,
                         "montant_total": [100.0] * n, "score_r": [5] * n, "score_f": [3] * n, "score_m": [3] * n})


def test_classement_par_rang_moyen_et_exclusion():
    n = 30
    trois = [i % 3 for i in range(n)]
    resultats = {
        # K-Means : meilleure silhouette ET meilleur DB, CH moyen -> rang moyen le plus bas
        "kmeans": _res("kmeans", trois, 0.60, 0.70, 800, k=3),
        "gmm": _res("gmm", trois, 0.50, 0.90, 900, k=3),
        # DBSCAN : 15 groupes -> écarté malgré une bonne silhouette
        "dbscan": _res("dbscan", [i % 15 for i in range(n)], 0.65, 0.60, 1000, eps=0.2),
    }
    lignes, meilleur, synthese = cs.comparer(resultats, _rfm(n), 365)
    par = {ligne["algorithme"]: ligne for ligne in lignes}
    assert meilleur == "kmeans"
    assert par["kmeans"]["rang"] == 1 and par["gmm"]["rang"] == 2
    assert par["dbscan"]["eligible"] is False and par["dbscan"]["rang"] is None
    assert any(t.startswith("Écarté : DBSCAN — 15 groupes") for t in synthese)
    assert any("même étiquette quel que soit l'algorithme" in t for t in synthese)


def test_aucun_algorithme_exploitable():
    n = 10
    resultats = {"kmeans": _res("kmeans", [0] * n, None, None, None, k=1)}
    lignes, meilleur, synthese = cs.comparer(resultats, _rfm(n), 365)
    assert meilleur is None and "Aucun algorithme" in synthese[0]


def test_silhouette_echantillonnee_au_dela_du_seuil(monkeypatch):
    """Au-delà de SILHOUETTE_ECHANTILLON points, la silhouette est estimée sur un
    échantillon fixe : résultat reproductible et proche de la valeur exacte."""
    from app.services import rfm_clustering_service as svc

    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(c, 0.5, size=(400, 2)) for c in (0, 4, 8)])
    labels = np.repeat([0, 1, 2], 400)
    exacte = svc._metriques_clustering(X, labels)[0]
    monkeypatch.setattr(svc, "SILHOUETTE_ECHANTILLON", 500)
    s1 = svc._metriques_clustering(X, labels)[0]
    s2 = svc._metriques_clustering(X, labels)[0]
    assert s1 == s2
    assert abs(s1 - exacte) < 0.02
