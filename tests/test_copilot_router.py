from app.core.config import settings
from app.models import Client, StockProduit
from app.services import copilot_service, llm_client


def test_chat_local_mode_returns_context_snapshot(client, db_session):
    resp = client.post("/api/v1/copilot/chat", json={"question": "Quel est le CA total ?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "Mode local" in body["reponse"]
    assert "CA total" in body["reponse"]
    assert body["sources"] == ["analytics_service", "marketing_service"]


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
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-legacy-test")

    fournisseur, cle, _modele = llm_client.resolve_provider()
    assert fournisseur == "anthropic"
    assert cle == "sk-legacy-test"


def test_chat_uses_llm_when_provider_configured(client, db_session, monkeypatch):
    """Depuis la tâche #6, Anthropic configuré passe par
    `generate_completion_with_tools` (mode agent) et non plus
    `generate_completion` (chat simple) — cf. `llm_client.supports_tool_calling`."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(
        copilot_service.llm_client,
        "generate_completion_with_tools",
        lambda system_prompt, messages, tools: {
            "stop_reason": "end_turn",
            "text": "Réponse générée par le LLM.",
            "tool_calls": [],
            "assistant_content": [{"type": "text", "text": "Réponse générée par le LLM."}],
        },
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
