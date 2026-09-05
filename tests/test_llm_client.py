"""Tests du client LLM multi-fournisseur (Phase 5).

Aucun appel réseau réel : `httpx.post` est monkeypatché pour retourner des
payloads JSON qui reproduisent EXACTEMENT le format documenté de chaque
fournisseur (Anthropic, OpenAI/Groq/OpenRouter, Google) — ce qui teste la
vraie logique d'extraction du texte (`_appel_*`), pas seulement le routage
déjà couvert par `test_copilot_router.py`. C'est la seule façon de
détecter un bug de parsing (mauvaise clé JSON, mauvais chemin d'accès)
sans dépendre d'une clé API réelle en CI.
"""

import httpx
import pytest

from app.services import llm_client


class _ReponseFactice:
    def __init__(self, status_code: int, payload: dict | None = None, texte: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = texte or str(payload)

    def json(self):
        return self._payload


def test_appel_anthropic_extrait_le_texte_du_bloc_content(monkeypatch):
    def _post_factice(url, headers, json, timeout):
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"] == "sk-ant-test"
        assert json["messages"][0]["content"] == "question utilisateur"
        return _ReponseFactice(200, {"content": [{"type": "text", "text": "Voici la réponse."}]})

    monkeypatch.setattr(httpx, "post", _post_factice)
    texte = llm_client._appel_anthropic("sk-ant-test", "claude-3-5-haiku-latest", "system", "question utilisateur")
    assert texte == "Voici la réponse."


def test_appel_anthropic_leve_llmerror_sur_reponse_sans_texte(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _ReponseFactice(200, {"content": []}))
    with pytest.raises(llm_client.LLMError):
        llm_client._appel_anthropic("sk-ant-test", "modele", "system", "question")


def test_appel_anthropic_leve_llmerror_sur_code_http_erreur(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: _ReponseFactice(401, texte="clé invalide")
    )
    with pytest.raises(llm_client.LLMError, match="401"):
        llm_client._appel_anthropic("sk-ant-invalide", "modele", "system", "question")


@pytest.mark.parametrize("fournisseur", ["openai", "groq", "openrouter"])
def test_appel_compatible_openai_extrait_le_texte_du_choix(monkeypatch, fournisseur):
    def _post_factice(url, headers, json, timeout):
        assert url == llm_client._URLS_COMPATIBLES_OPENAI[fournisseur]
        assert headers["Authorization"] == "Bearer cle-test"
        assert json["messages"][0]["role"] == "system"
        return _ReponseFactice(200, {"choices": [{"message": {"content": "Réponse générée."}}]})

    monkeypatch.setattr(httpx, "post", _post_factice)
    texte = llm_client._appel_compatible_openai(fournisseur, "cle-test", "modele", "system", "question")
    assert texte == "Réponse générée."


def test_appel_google_extrait_le_texte_des_parts(monkeypatch):
    def _post_factice(url, params, headers, json, timeout):
        assert "generateContent" in url
        assert params["key"] == "google-test"
        return _ReponseFactice(
            200,
            {"candidates": [{"content": {"parts": [{"text": "Bon"}, {"text": "jour"}]}}]},
        )

    monkeypatch.setattr(httpx, "post", _post_factice)
    texte = llm_client._appel_google("google-test", "gemini-1.5-flash", "system", "question")
    assert texte == "Bonjour"


def test_generate_completion_wraps_network_errors_as_llmerror(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")

    def _post_qui_echoue(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout simulé")

    monkeypatch.setattr(httpx, "post", _post_qui_echoue)

    with pytest.raises(llm_client.LLMError, match="anthropic"):
        llm_client.generate_completion("system", "question")


def test_generate_completion_raises_when_no_provider_configured(monkeypatch):
    from app.core.config import settings

    for env_name, _nom, _modele in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", None)

    with pytest.raises(llm_client.LLMError):
        llm_client.generate_completion("system", "question")
