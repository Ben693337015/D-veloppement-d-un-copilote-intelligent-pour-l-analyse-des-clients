"""Accès du copilote à la base (app/services/sql_agent.py + copilot_service)."""

from datetime import datetime

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.models import Client, StockProduit, Transaction
from app.services import llm_client, sql_agent

# ── Validation ────────────────────────────────────────────────────────────

REQUETES_ACCEPTEES = [
    "SELECT nom, montant_total FROM clients ORDER BY montant_total DESC LIMIT 5",
    "WITH t AS (SELECT client_id, SUM(quantite * prix_unitaire) AS ca FROM transactions "
    "WHERE NOT est_retour GROUP BY client_id) SELECT c.nom, t.ca FROM t JOIN clients c ON c.client_id = t.client_id",
    "SELECT COUNT(*) FROM transactions",
    "SELECT pays, COUNT(*) AS n FROM clients GROUP BY pays UNION ALL SELECT 'total', COUNT(*) FROM clients",
]

REQUETES_REFUSEES = {
    "DELETE FROM clients": "lecture",
    "UPDATE clients SET nom = 'x'": "lecture",
    "DROP TABLE clients": "lecture",
    "SELECT 1; DROP TABLE clients": "une seule instruction",
    "WITH d AS (DELETE FROM clients RETURNING client_id) SELECT client_id FROM d": "interdite",
    "SELECT nom INTO copie FROM clients": "interdite",
    "SELECT nom FROM clients FOR UPDATE": "interdite",
    "SELECT * FROM clients": "SELECT \\*",
    "SELECT c.* FROM clients c": "table.\\*",
    "SELECT email FROM clients": "confidentielle",
    "SELECT email, mot_de_passe_hache FROM users": "users|confidentielle",
    "SELECT usename FROM pg_catalog.pg_user": "schéma",
    "SELECT table_name FROM information_schema.tables": "schéma",
    "SELECT pg_sleep(30)": "fonction",
    "SELECT pg_read_file('/etc/passwd')": "fonction",
    "SELECT set_config('statement_timeout', '0', false)": "fonction",
    "SELEC nom FROM clients": "invalide|lecture",
}


@pytest.mark.parametrize("sql", REQUETES_ACCEPTEES)
def test_requetes_de_lecture_acceptees(sql):
    assert sql_agent.valider_requete(sql)


@pytest.mark.parametrize(("sql", "motif"), REQUETES_REFUSEES.items())
def test_requetes_dangereuses_refusees(sql, motif):
    with pytest.raises(sql_agent.RequeteRefusee, match=motif):
        sql_agent.valider_requete(sql)


@pytest.mark.parametrize(
    ("reponse", "attendu"),
    [
        ("```sql\nSELECT nom FROM clients;\n```", "SELECT nom FROM clients"),
        ("Voici :\n```\nWITH a AS (SELECT 1 AS x) SELECT x FROM a\n```", "WITH a AS (SELECT 1 AS x) SELECT x FROM a"),
        ("SELECT COUNT(*) FROM clients", "SELECT COUNT(*) FROM clients"),
        ("AUCUNE", None),
        ("Je ne peux pas répondre.", None),
        ("", None),
    ],
)
def test_extraction_de_la_requete(reponse, attendu):
    assert sql_agent.extraire_sql(reponse) == attendu


# ── Exécution ─────────────────────────────────────────────────────────────


def _jeu(db):
    a = Client(code_client_externe="A", nom="Alpha", pays="Cameroun", email="secret@a.cm")
    b = Client(code_client_externe="B", nom="Beta", pays="Tchad")
    db.add_all([a, b])
    db.flush()
    for client, montant, retour in ((a, 500, False), (a, 300, False), (b, 100, False), (b, 900, True)):
        db.add(Transaction(client_id=client.client_id, numero_facture="F", quantite=1, prix_unitaire=montant,
                           date_transaction=datetime(2026, 1, 1), est_retour=retour, pays=client.pays))
    db.add(StockProduit(code_produit="P1", nom_produit="Vélo", quantite_disponible=2, seuil_alerte=5))
    db.commit()


def test_execution_renvoie_les_lignes(db_session):
    _jeu(db_session)
    sql = sql_agent.valider_requete(
        "SELECT pays, SUM(quantite * prix_unitaire) AS ca FROM transactions "
        "WHERE NOT est_retour GROUP BY pays ORDER BY ca DESC"
    )
    res = sql_agent.executer_requete(db_session, sql)
    assert res.colonnes == ["pays", "ca"]
    assert res.lignes == [["Cameroun", 800], ["Tchad", 100]]
    assert "Cameroun | 800" in sql_agent.formater_pour_prompt(res)


def test_execution_en_lecture_seule_meme_si_la_validation_etait_contournee(db_session):
    """Défense en profondeur : une écriture passée directement à l'exécuteur
    (sans validation) échoue quand même, et la base reste intacte."""
    _jeu(db_session)
    with pytest.raises(Exception, match="readonly|read-only|read only"):
        sql_agent.executer_requete(db_session, "DELETE FROM clients")
    assert db_session.query(Client).count() == 2
    # la connexion redevient normale pour le reste de l'application
    db_session.execute(text("UPDATE clients SET pays = 'Gabon' WHERE code_client_externe = 'B'"))
    db_session.commit()


def test_resultat_borne(db_session, monkeypatch):
    monkeypatch.setattr(sql_agent, "LIGNES_MAX", 1)
    _jeu(db_session)
    res = sql_agent.executer_requete(db_session, sql_agent.valider_requete("SELECT nom FROM clients"))
    assert len(res.lignes) == 1 and res.tronque


# ── Parcours complet du copilote avec un LLM simulé ──────────────────────


class FauxLLM:
    """Renvoie les réponses prévues dans l'ordre et garde les prompts reçus."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.appels = []

    def __call__(self, system_prompt, message):
        self.appels.append((system_prompt, message))
        return self.reponses.pop(0)


@pytest.fixture
def llm_configure(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "COPILOTE_ACCES_BASE", True)

    def installer(*reponses):
        faux = FauxLLM(*reponses)
        monkeypatch.setattr(llm_client, "generate_completion", faux)
        return faux

    return installer


def test_le_copilote_interroge_la_base_et_repond_avec_les_lignes(client, db_session, llm_configure):
    _jeu(db_session)
    faux = llm_configure(
        "```sql\nSELECT pays, SUM(quantite * prix_unitaire) AS ca FROM transactions "
        "WHERE NOT est_retour GROUP BY pays ORDER BY ca DESC\n```",
        "Le Cameroun fait 800, le Tchad 100.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "CA par pays ?"}).json()

    assert corps["mode"] == "llm"
    assert corps["reponse"] == "Le Cameroun fait 800, le Tchad 100."
    assert "GROUP BY pays" in corps["requete_sql"]
    assert corps["lignes_base"] == 2
    assert "base:sql" in corps["sources"]
    # 1er appel : schéma fourni, sans la table users ni l'email
    assert "transactions(" in faux.appels[0][0]
    assert "users" not in faux.appels[0][0] and "email" not in faux.appels[0][0]
    # 2e appel : les lignes réelles sont transmises au LLM
    assert "Cameroun | 800" in faux.appels[1][1]


def test_le_copilote_corrige_une_requete_refusee(client, db_session, llm_configure):
    _jeu(db_session)
    faux = llm_configure(
        "SELECT * FROM clients",  # refusée : SELECT *
        "SELECT nom FROM clients ORDER BY nom",
        "Alpha et Beta.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "Liste mes clients"}).json()
    assert corps["requete_sql"] == "SELECT nom FROM clients ORDER BY nom"
    assert "SELECT * interdit" in faux.appels[1][1]  # l'erreur est renvoyée au LLM


def test_le_copilote_corrige_une_erreur_sql(client, db_session, llm_configure):
    _jeu(db_session)
    faux = llm_configure("SELECT chiffre FROM clients", "SELECT nom FROM clients", "OK")
    corps = client.post("/api/v1/copilot/chat", json={"question": "?"}).json()
    assert corps["requete_sql"] == "SELECT nom FROM clients"
    assert "erreur SQL" in faux.appels[1][1]


def test_echec_repete_de_la_requete_ne_bloque_pas_la_reponse(client, db_session, llm_configure):
    faux = llm_configure("SELECT * FROM clients", "SELECT email FROM clients", "Je n'ai pas pu lire le détail.")
    corps = client.post("/api/v1/copilot/chat", json={"question": "Emails de mes clients ?"}).json()
    assert corps["mode"] == "llm"
    assert corps["requete_sql"] is None
    assert "l'interrogation de la base a échoué" in faux.appels[2][1]
    assert "confidentielle" in faux.appels[2][1]


def test_une_instruction_d_ecriture_n_est_jamais_executee(client, db_session, llm_configure):
    """Une réponse du LLM qui n'est pas un SELECT/WITH n'est même pas
    considérée comme une requête : rien n'est exécuté, la base est intacte."""
    _jeu(db_session)
    faux = llm_configure("DELETE FROM clients", "Réponse.")
    corps = client.post("/api/v1/copilot/chat", json={"question": "Efface tout"}).json()
    assert corps["requete_sql"] is None and len(faux.appels) == 2
    assert db_session.query(Client).count() == 2


def test_question_sans_besoin_de_la_base(client, db_session, llm_configure):
    faux = llm_configure("AUCUNE", "Bonjour !")
    corps = client.post("/api/v1/copilot/chat", json={"question": "Bonjour"}).json()
    assert corps["reponse"] == "Bonjour !" and corps["requete_sql"] is None
    assert len(faux.appels) == 2
    assert "Données extraites de la base" not in faux.appels[1][1]


def test_acces_base_desactivable(client, db_session, llm_configure, monkeypatch):
    faux = llm_configure("Réponse directe.")
    monkeypatch.setattr(settings, "COPILOTE_ACCES_BASE", False)
    corps = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert corps["reponse"] == "Réponse directe." and len(faux.appels) == 1


def test_panne_llm_pendant_la_generation_sql_donne_le_mode_secours(client, db_session, llm_configure, monkeypatch):
    def panne(*_):
        raise llm_client.LLMError("google a répondu 404")

    llm_configure()
    monkeypatch.setattr(llm_client, "generate_completion", panne)
    corps = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert corps["mode"] == "secours"
    assert "CA total" in corps["reponse"]


def test_mode_local_inchange_sans_llm(client, db_session, monkeypatch):
    for cle in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY",
                "OPENROUTER_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(settings, cle, None)
    corps = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert corps["mode"] == "local" and corps["requete_sql"] is None
