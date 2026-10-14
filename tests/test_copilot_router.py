from datetime import datetime

import pytest

from app.core.config import settings
from app.models import Client, StockProduit
from app.services import copilot_service, llm_client


def test_chat_local_mode_returns_context_snapshot(client, db_session):
    resp = client.post("/api/v1/copilot/chat", json={"question": "Quel est le CA total ?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "Mode local" in body["reponse"]
    assert "CA total" in body["reponse"]
    assert body["sources"] == ["analytics_service", "marketing_service", "forecasting_service"]


def test_chat_reflects_actual_database_state(client, db_session):
    db_session.add(
        Client(code_client_externe="C1", segment_rfm="Fidèle", montant_total=500.0, score_churn=0.1)
    )
    db_session.add(
        StockProduit(code_produit="P1", quantite_disponible=1, seuil_alerte=10, seuil_reapprovisionnement=20)
    )
    db_session.commit()

    resp = client.post("/api/v1/copilot/chat", json={"question": "Comment vont mes stocks ?"})
    assert resp.status_code == 200
    reponse = resp.json()["reponse"]
    assert "Clients: 1" in reponse
    assert "Segment Fidèle: 1 clients" in reponse


def test_chat_empty_database_does_not_crash(client):
    resp = client.post("/api/v1/copilot/chat", json={"question": "Combien de clients ?"})
    assert resp.status_code == 200
    assert "Clients: 0" in resp.json()["reponse"]


def test_chat_context_covers_every_analysis_module(client, db_session):
    """NOUVEAU COMPORTEMENT : le contexte injecté dans le prompt doit couvrir
    TOUTES les analyses déjà calculées ailleurs dans l'application (RFM +
    échantillon de clients, prévision des ventes, prévision de trésorerie +
    alerte) — pas seulement les KPIs et le nombre d'alertes stock comme
    auparavant — pour que le copilote puisse répondre à une question sur
    n'importe quel module sans halluciner une donnée absente de son contexte."""
    from datetime import timedelta

    from app.models import Client, Transaction

    base = datetime.utcnow() - timedelta(days=40)
    for i in range(6):
        c = Client(code_client_externe=f"C{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(4):
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"F{i}-{j}",
                    quantite=1,
                    prix_unitaire=100.0 * (i + 1),
                    date_transaction=base + timedelta(days=j * 7 + i),
                )
            )
    db_session.commit()

    from app.services import rfm_clustering_service

    rfm_clustering_service.run_full_pipeline(db_session, dry_run=False)

    resp = client.post("/api/v1/copilot/chat", json={"question": "Fais-moi un état complet"})
    assert resp.status_code == 200
    reponse = resp.json()["reponse"]

    # Segmentation RFM + échantillon de clients cités nommément.
    assert "Segmentation RFM" in reponse
    assert "Exemples (" in reponse
    # Prévision des ventes (modèle, RMSE/MAE) — absente du contexte avant cet ajout.
    assert "Prévision des ventes" in reponse
    assert "modèle :" in reponse
    # Prévision de trésorerie + niveau d'alerte — idem.
    assert "Prévision de trésorerie" in reponse
    assert "niveau d'alerte" in reponse


# ---------------------------------------------------------------------------
# Intégration LLM réelle (Phase 5) — jamais d'appel réseau dans les tests
# automatisés (lent, non déterministe, nécessiterait une vraie clé) : le
# client LLM est simulé via monkeypatch de `llm_client.generate_completion`,
# ce qui teste le VRAI chemin d'intégration (résolution du fournisseur,
# construction du prompt, gestion des erreurs) sans dépendance externe.
# ---------------------------------------------------------------------------


def test_resolve_provider_returns_none_without_any_key(monkeypatch):
    for env_name, _nom, _modele in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    assert llm_client.resolve_provider() is None


def test_resolve_provider_picks_first_configured_by_priority(monkeypatch):
    for env_name, _nom, _modele in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google_test")

    fournisseur, cle, _modele = llm_client.resolve_provider()
    # groq précède google dans l'ordre de priorité (cf. _FOURNISSEURS_PRIORITE)
    assert fournisseur == "groq"
    assert cle == "gsk_test"


def test_resolve_provider_respects_explicit_llm_provider_override(monkeypatch):
    for env_name, _nom, _modele in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-oai-test")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")

    fournisseur, cle, _modele = llm_client.resolve_provider()
    assert fournisseur == "openai"
    assert cle == "sk-oai-test"


def test_resolve_provider_falls_back_to_generic_llm_api_key_as_anthropic(monkeypatch):
    for env_name, _nom, _modele in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-ant-legacy-test")

    fournisseur, cle, _modele = llm_client.resolve_provider()
    assert fournisseur == "anthropic"
    assert cle == "sk-ant-legacy-test"


def test_chat_uses_llm_when_provider_configured(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(
        copilot_service.llm_client,
        "generate_completion",
        lambda system_prompt, user_message: "Réponse générée par le LLM.",
    )

    resp = client.post("/api/v1/copilot/chat", json={"question": "Quel est le CA ?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reponse"] == "Réponse générée par le LLM."
    assert "llm:anthropic" in body["sources"]


def test_chat_never_crashes_when_llm_call_fails(client, db_session, monkeypatch):
    """BUG HISTORIQUE CORRIGÉ (verrouillé pour l'intégration réelle) : un
    appel LLM qui échoue (clé invalide, réseau indisponible, quota dépassé,
    réponse mal formée...) ne doit JAMAIS faire planter l'endpoint — bascule
    systématique sur la synthèse factuelle locale, motif d'échec explicite."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-invalid")

    def _echoue(system_prompt, user_message):
        raise llm_client.LLMError("anthropic a répondu 401 (clé invalide)")

    monkeypatch.setattr(copilot_service.llm_client, "generate_completion", _echoue)

    resp = client.post("/api/v1/copilot/chat", json={"question": "Test"})
    assert resp.status_code == 200
    reponse = resp.json()["reponse"]
    assert "anthropic" in reponse
    assert "CA total" in reponse  # repli sur la synthèse factuelle, toujours présente


def test_chat_llm_error_never_raises_uncaught_exception(client, db_session, monkeypatch):
    """Même vérification que ci-dessus mais via un appel HTTP direct (pas
    d'accès à `db_session` en dehors de la requête) pour s'assurer que
    FastAPI ne laisse fuiter aucune trace brute au client."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_invalid")

    def _echoue(system_prompt, user_message):
        raise llm_client.LLMError("erreur réseau vers groq (timeout)")

    monkeypatch.setattr(copilot_service.llm_client, "generate_completion", _echoue)

    resp = client.post("/api/v1/copilot/chat", json={"question": "Autre test"})
    assert resp.status_code == 200
    assert "traceback" not in resp.text.lower()


# ── Corrections issues du test de bout en bout ─────────────────────────────

def test_chat_indique_l_origine_de_la_reponse(client, db_session, monkeypatch):
    for env_name, _, _ in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)
    local = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert (local["mode"], local["fournisseur"]) == ("local", None)

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-test")
    monkeypatch.setattr(copilot_service.llm_client, "generate_completion", lambda s, u: "Réponse LLM")
    llm = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert (llm["mode"], llm["fournisseur"], llm["modele"]) == ("llm", "openai", "gpt-test")

    def _echoue(s, u):
        raise llm_client.LLMError("panne")

    monkeypatch.setattr(copilot_service.llm_client, "generate_completion", _echoue)
    secours = client.post("/api/v1/copilot/chat", json={"question": "CA ?"}).json()
    assert secours["mode"] == "secours" and "CA total" in secours["reponse"]


@pytest.mark.parametrize("question", ["", "x" * 2001])
def test_chat_refuse_question_vide_ou_demesuree(client, question):
    assert client.post("/api/v1/copilot/chat", json={"question": question}).status_code == 422


def test_contexte_stock_borne_et_critiques_en_premier(db_session):
    for i in range(60):
        # i < 5 : critiques (stock <= seuil 10) ; les autres en « attention »
        db_session.add(StockProduit(code_produit=f"P{i:03d}", nom_produit=f"Produit {i}",
                                    quantite_disponible=2 if i < 5 else 15,
                                    seuil_alerte=10, seuil_reapprovisionnement=20))
    db_session.commit()
    lignes = copilot_service._section_stock(db_session)
    assert lignes[0] == "Alertes stock actives: 60 (critique: 5, attention: 55)"
    details = [line for line in lignes[1:] if "niveau" in line]
    assert len(details) == copilot_service._N_ALERTES_STOCK_CONTEXTE
    assert all("niveau critique" in line for line in details[:5])
    assert "35 autre(s) alerte(s)" in lignes[-1]


# ── Classement des clients par CA (question « quels clients font mon CA ? ») ──


def _client_avec_ventes(db, code, montants, retour=None):
    from app.models import Transaction

    c = Client(code_client_externe=code, nom=f"Société {code}")
    db.add(c)
    db.flush()
    for i, m in enumerate(montants):
        db.add(Transaction(client_id=c.client_id, numero_facture=f"{code}-{i}", quantite=1,
                           prix_unitaire=m, date_transaction=datetime(2026, 1, 1 + i)))
    if retour:
        db.add(Transaction(client_id=c.client_id, numero_facture=f"{code}-R", quantite=1,
                           prix_unitaire=retour, date_transaction=datetime(2026, 2, 1), est_retour=True))
    return c


def test_top_clients_classe_par_ca_exclut_retours_et_calcule_pareto(db_session):
    from app.services import analytics_service

    _client_avec_ventes(db_session, "A", [700, 100], retour=5000)  # le retour ne compte pas
    _client_avec_ventes(db_session, "B", [150])
    _client_avec_ventes(db_session, "C", [50])
    db_session.commit()

    res = analytics_service.get_top_clients_ca(db_session, limite=2)
    assert [c["client"] for c in res["top"]] == ["Société A", "Société B"]
    assert res["top"][0]["chiffre_affaires"] == 800
    assert res["top"][0]["nb_factures"] == 2
    assert res["ca_total_clients"] == 1000
    assert res["top"][0]["part_ca"] == pytest.approx(0.8)
    assert res["nb_clients"] == 3
    assert res["nb_clients_80pct"] == 1  # A seul fait 80 % du CA


def test_contexte_copilote_contient_le_top_clients(client, db_session):
    _client_avec_ventes(db_session, "GROS", [9000])
    _client_avec_ventes(db_session, "PETIT", [10])
    db_session.commit()
    reponse = client.post("/api/v1/copilot/chat", json={"question": "Quels clients font mon CA ?"}).json()["reponse"]
    assert "Top 2 clients par chiffre d'affaires" in reponse
    assert reponse.index("1. Société GROS") < reponse.index("2. Société PETIT")
    assert "réalisent 80 % du CA" in reponse


def test_top_clients_base_vide(db_session):
    from app.services import analytics_service

    assert analytics_service.get_top_clients_ca(db_session)["top"] == []
    assert copilot_service._section_top_clients(db_session) == [
        "Classement des clients par CA : aucun client rattaché à une vente."
    ]


def test_exemples_de_segment_tries_par_montant(db_session):
    from app.services import marketing_service

    for code, montant in (("X", 10.0), ("Y", 900.0), ("Z", 300.0)):
        db_session.add(Client(code_client_externe=code, segment_rfm="Occasionnel", montant_total=montant))
    db_session.commit()
    codes = [c.code_client_externe for c in marketing_service.list_clients_rfm(db_session, segment="Occasionnel")]
    assert codes == ["Y", "Z", "X"]
