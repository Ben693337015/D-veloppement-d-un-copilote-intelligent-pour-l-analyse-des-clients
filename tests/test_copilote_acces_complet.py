"""Le copilote a accès à toutes les données de l'application (13/10/2026) :
historique des segmentations, prévisions des 3 modèles, plusieurs requêtes
SQL par question, e-mails sur option, jamais les mots de passe."""

import json

import pandas as pd
import pytest
from sqlalchemy import inspect

from app.core.config import settings
from app.models import Client, HistoriqueSegmentation, Transaction
from app.services import copilot_service, llm_client, sql_agent


def _seed(db):
    base = pd.Timestamp("2024-06-01")
    for i in range(6):
        c = Client(code_client_externe=f"C{i}", nom=f"Client {i}", email=f"c{i}@pme.cm")
        db.add(c)
        db.flush()
        for j in range(3):
            db.add(Transaction(client_id=c.client_id, numero_facture=f"F{i}-{j}", quantite=2,
                               prix_unitaire=100.0 + i * 10, code_produit=f"P{j}",
                               date_transaction=base - pd.Timedelta(days=j * 10 + i)))
    db.commit()


# ── Historique des segmentations ────────────────────────────────────────────


def test_chaque_segmentation_est_enregistree(client, db_session):
    _seed(db_session)
    corps = client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 4}).json()
    lignes = db_session.query(HistoriqueSegmentation).all()
    assert len(lignes) == 1
    h = lignes[0]
    assert h.algorithme == "kmeans" and h.n_clients == 6 and h.k_choisi == corps["k_choisi"]
    assert h.silhouette == pytest.approx(corps["silhouette_choisi"])
    assert [d["k"] for d in json.loads(h.diagnostics)] == [2, 3, 4]
    assert sum(json.loads(h.repartition_segments).values()) == 6


def test_simulation_dry_run_non_enregistree(client, db_session):
    _seed(db_session)
    client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 3, "dry_run": True})
    assert db_session.query(HistoriqueSegmentation).count() == 0


def test_contexte_contient_segmentation_et_previsions_3_modeles(client, db_session):
    _seed(db_session)
    client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 3})
    contexte = copilot_service.build_context_snapshot(db_session)
    assert "Dernière segmentation" in contexte and "Diagnostic par k testé" in contexte
    # Lecture métier en tête (corrections22), détail des 3 modèles conservé et signalé comme technique.
    assert "Prévision des ventes à 30 jours (à présenter en langage métier)" in contexte
    assert "[Détail technique" in contexte
    for nom in ("Prophet", "ARIMA", "XGBoost"):
        assert f"  - {nom} (" in contexte


def test_creation_de_la_table_au_demarrage_pour_une_base_existante(db_session, monkeypatch):
    import app.core.database as database
    from app.main import _creer_tables_ajoutees

    moteur = db_session.get_bind()
    HistoriqueSegmentation.__table__.drop(bind=moteur)
    assert "historique_segmentation" not in inspect(moteur).get_table_names()
    monkeypatch.setattr(database, "engine", moteur)
    _creer_tables_ajoutees()
    assert "historique_segmentation" in inspect(moteur).get_table_names()


def test_demarrage_jamais_bloque_si_base_injoignable(monkeypatch):
    from sqlalchemy import create_engine

    import app.core.database as database
    from app.main import _creer_tables_ajoutees

    monkeypatch.setattr(database, "engine", create_engine("postgresql+psycopg2://x:y@127.0.0.1:1/z"))
    _creer_tables_ajoutees()  # ne lève pas


# ── Tables, colonnes et e-mails ─────────────────────────────────────────────


def test_historique_segmentation_interrogeable_en_sql():
    assert sql_agent.valider_requete(
        "SELECT date_execution, k_choisi, silhouette FROM historique_segmentation "
        "ORDER BY date_execution DESC LIMIT 1"
    )
    assert "historique_segmentation(" in sql_agent.schema_texte()


def test_emails_interdits_par_defaut_autorises_sur_option(monkeypatch):
    monkeypatch.setattr(settings, "COPILOTE_ACCES_EMAILS", False)
    with pytest.raises(sql_agent.RequeteRefusee, match="confidentielle"):
        sql_agent.valider_requete("SELECT nom, email FROM clients")
    assert "email" not in sql_agent.schema_texte()

    monkeypatch.setattr(settings, "COPILOTE_ACCES_EMAILS", True)
    assert sql_agent.valider_requete("SELECT nom, email FROM clients")
    assert "email : e-mail du client" in sql_agent.schema_texte()
    # les mots de passe et la table users restent inaccessibles
    with pytest.raises(sql_agent.RequeteRefusee):
        sql_agent.valider_requete("SELECT mot_de_passe_hache FROM users")
    with pytest.raises(sql_agent.RequeteRefusee, match="users"):
        sql_agent.valider_requete("SELECT nom FROM users")


# ── Plusieurs requêtes par question ─────────────────────────────────────────


def test_extraction_de_plusieurs_requetes():
    reponse = "```sql\nSELECT 1 AS a\n```\ntexte\n```sql\nSELECT 2 AS b;\n```\n```sql\nDELETE FROM clients\n```"
    assert sql_agent.extraire_requetes(reponse) == ["SELECT 1 AS a", "SELECT 2 AS b"]
    blocs = "\n".join(f"```sql\nSELECT {i} AS x\n```" for i in range(5))
    assert len(sql_agent.extraire_requetes(blocs)) == sql_agent.REQUETES_MAX


class FauxLLM:
    def __init__(self, *reponses):
        self.reponses, self.appels = list(reponses), []

    def __call__(self, system, message):
        self.appels.append((system, message))
        return self.reponses.pop(0)


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "COPILOTE_ACCES_BASE", True)

    def installer(*reponses):
        faux = FauxLLM(*reponses)
        monkeypatch.setattr(llm_client, "generate_completion", faux)
        return faux

    return installer


def test_question_croisant_deux_sources(client, db_session, llm):
    _seed(db_session)
    faux = llm(
        "```sql\nSELECT code_produit, SUM(quantite) AS vendu FROM transactions GROUP BY code_produit ORDER BY code_produit\n```\n"
        "```sql\nSELECT COUNT(*) AS nb_clients FROM clients\n```",
        "Réponse croisée.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "Ventes par produit et nombre de clients ?"}).json()
    assert corps["reponse"] == "Réponse croisée."
    assert len(corps["requetes_sql"]) == 2
    assert corps["lignes_base"] == 4  # 3 produits + 1 ligne de comptage
    final = faux.appels[-1][1]
    assert "requête 1" in final and "requête 2" in final and "nb_clients" in final


def test_seule_la_requete_en_echec_est_corrigee(client, db_session, llm):
    _seed(db_session)
    faux = llm(
        "```sql\nSELECT COUNT(*) AS n FROM clients\n```\n```sql\nSELECT inexistante FROM transactions\n```",
        "```sql\nSELECT COUNT(*) AS n_ventes FROM transactions\n```",
        "OK.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "?"}).json()
    assert corps["requetes_sql"] == ["SELECT COUNT(*) AS n FROM clients", "SELECT COUNT(*) AS n_ventes FROM transactions"]
    assert "inexistante" in faux.appels[1][1] and "COUNT(*) AS n FROM clients" not in faux.appels[1][1]
    assert "a échoué" not in faux.appels[2][1].split("Question :")[-1]


def test_prompt_sql_mentionne_le_schema_complet(llm):
    prompt = copilot_service.system_prompt_sql()
    for table in ("clients(", "transactions(", "stocks_produits(", "tresorerie(", "historique_segmentation("):
        assert table in prompt
    assert "users" not in prompt and "mot_de_passe" not in prompt
