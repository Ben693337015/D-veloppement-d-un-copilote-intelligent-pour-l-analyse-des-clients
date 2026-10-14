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
    texte = llm_client._appel_anthropic("sk-ant-test", "claude-haiku-5-5", "system", "question utilisateur")
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
    def _post_factice(url, headers, json, timeout):
        assert "generateContent" in url
        # clé dans l'en-tête officiel (compatible clés « AQ. » et « AIza »), jamais dans l'URL
        assert headers["x-goog-api-key"] == "google-test"
        assert "google-test" not in url
        return _ReponseFactice(
            200,
            {"candidates": [{"content": {"parts": [{"text": "Bon"}, {"text": "jour"}]}}]},
        )

    monkeypatch.setattr(httpx, "post", _post_factice)
    texte = llm_client._appel_google("google-test", "gemini-3.6-flash", "system", "question")
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


# ── LLM_BASE_URL : LLM local (Ollama, LM Studio), proxy ou serveur simulé ──

@pytest.mark.parametrize(
    ("fournisseur", "base", "attendu"),
    [
        ("openai", None, "https://api.openai.com/v1/chat/completions"),
        ("groq", None, "https://api.groq.com/openai/v1/chat/completions"),
        ("anthropic", None, "https://api.anthropic.com/v1/messages"),
        ("openai", "http://localhost:11434/v1/", "http://localhost:11434/v1/chat/completions"),
        ("anthropic", "http://proxy.local/v1", "http://proxy.local/v1/messages"),
    ],
)
def test_url_appel_respecte_llm_base_url(monkeypatch, fournisseur, base, attendu):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_BASE_URL", base)
    suffixe = "/messages" if fournisseur == "anthropic" else "/chat/completions"
    assert llm_client._url(fournisseur, suffixe) == attendu


def test_generate_completion_utilise_llm_base_url(monkeypatch):
    from app.core.config import settings

    for env_name, _, _ in llm_client._FOURNISSEURS_PRIORITE:
        monkeypatch.setattr(settings, env_name, None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "llama3.1")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    appels = []

    def _post(url, **kw):
        appels.append((url, kw["json"]["model"]))
        return _ReponseFactice(200, {"choices": [{"message": {"content": "ok local"}}]})

    monkeypatch.setattr(httpx, "post", _post)
    assert llm_client.generate_completion("sys", "question") == "ok local"
    assert appels == [("http://localhost:11434/v1/chat/completions", "llama3.1")]


# ── Modèles par défaut (octobre 2026) et message d'erreur 404 ──


def test_modeles_par_defaut_ne_sont_plus_des_modeles_retires():
    defauts = {nom: modele for _, nom, modele in llm_client._FOURNISSEURS_PRIORITE}
    retires = {"claude-3-5-haiku-latest", "gemini-1.5-flash", "llama-3.1-8b-instant",
               "meta-llama/llama-3.1-8b-instruct:free"}
    assert not retires & set(defauts.values())
    assert defauts["google"] == "gemini-3.6-flash"
    assert defauts["anthropic"] == "claude-haiku-5-5"


def test_erreur_404_guide_vers_llm_model(monkeypatch):
    import httpx

    def faux_post(url, **kwargs):
        return httpx.Response(404, text='{"error": {"code": 404, "message": "models/x is not found"}}',
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(llm_client.httpx, "post", faux_post)
    with pytest.raises(llm_client.LLMError, match="LLM_MODEL"):
        llm_client._appel_google("cle", "gemini-1.5-flash", "system", "question")


def test_cle_refusee_message_explicite(monkeypatch):
    def faux_post(url, **kwargs):
        return httpx.Response(401, text='{"error": {"status": "UNAUTHENTICATED"}}',
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(llm_client.httpx, "post", faux_post)
    with pytest.raises(llm_client.LLMError, match="refusé la clé API"):
        llm_client._appel_google("AQ.cle-test", "gemini-3.6-flash", "system", "question")


# ── Surcharge temporaire (503 « high demand ») : nouvelles tentatives et secours ──


class _Serveur:
    """Faux fournisseur : renvoie une suite de codes HTTP, appel après appel."""

    def __init__(self, codes):
        self.codes = list(codes)
        self.modeles = []

    def __call__(self, url, **kwargs):
        self.modeles.append(url.split("/models/")[1].split(":")[0])
        code = self.codes.pop(0)
        corps = ('{"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}' if code == 200
                 else '{"error": {"code": 503, "message": "This model is currently experiencing high demand."}}')
        return httpx.Response(code, text=corps, request=httpx.Request("POST", url))


@pytest.fixture
def google(monkeypatch):
    from app.core.config import settings

    for cle in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "LLM_API_KEY",
                "LLM_MODEL", "LLM_PROVIDER", "LLM_BASE_URL"):
        monkeypatch.setattr(settings, cle, None)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AQ.test")
    attentes = []
    monkeypatch.setattr(llm_client.time, "sleep", attentes.append)

    def installer(codes):
        serveur = _Serveur(codes)
        monkeypatch.setattr(llm_client.httpx, "post", serveur)
        return serveur, attentes

    return installer


def test_503_puis_succes_sur_le_meme_modele(google):
    serveur, attentes = google([503, 200])
    assert llm_client.generate_completion("s", "q") == "OK"
    assert serveur.modeles == ["gemini-3.6-flash", "gemini-3.6-flash"]
    assert attentes == [1.5]
    assert llm_client.dernier_modele_utilise() == "gemini-3.6-flash"


def test_modele_de_secours_si_le_principal_reste_surcharge(google):
    serveur, attentes = google([503, 503, 503, 200])
    assert llm_client.generate_completion("s", "q") == "OK"
    assert serveur.modeles == ["gemini-3.6-flash"] * 3 + ["gemini-3.5-flash-lite"]
    assert llm_client.dernier_modele_utilise() == "gemini-3.5-flash-lite"


def test_tout_est_surcharge_message_clair(google):
    serveur, _ = google([503] * 6)
    with pytest.raises(llm_client.LLMError, match="momentanément surchargé"):
        llm_client.generate_completion("s", "q")
    assert len(serveur.modeles) == 6  # 3 tentatives × 2 modèles, puis abandon


def test_erreur_definitive_pas_de_nouvelle_tentative(google):
    serveur, attentes = google([401])
    with pytest.raises(llm_client.LLMError, match="refusé la clé"):
        llm_client.generate_completion("s", "q")
    assert len(serveur.modeles) == 1 and attentes == []


def test_retry_after_respecte_et_plafonne(google):
    serveur, attentes = google([429, 200])
    original = serveur.__call__

    def avec_entete(url, **kwargs):
        r = original(url, **kwargs)
        if r.status_code == 429:
            r.headers["retry-after"] = "30"
        return r

    llm_client.httpx.post = avec_entete
    assert llm_client.generate_completion("s", "q") == "OK"
    assert attentes == [llm_client.ATTENTE_MAX_SECONDES]
