"""Changer de fournisseur LLM en changeant SEULEMENT la clé API (.env).

Chaque scénario part d'un .env « vide » (aucune clé, pas de LLM_PROVIDER,
LLM_MODEL ni LLM_BASE_URL), renseigne une seule clé, puis vérifie de bout
en bout : fournisseur retenu, modèle par défaut, URL officielle appelée,
en-tête d'authentification et extraction du texte de la réponse. Les
appels réseau sont simulés (httpx.post), aucune vraie clé n'est utilisée.
"""

import httpx
import pytest

from app.core.config import settings
from app.services import llm_client

VARIABLES = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY",
             "OPENROUTER_API_KEY", "LLM_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"]

# (variable, clé au format réel, fournisseur, modèle par défaut, URL officielle, en-tête d'auth)
SCENARIOS = [
    ("ANTHROPIC_API_KEY", "sk-ant-api03-xxxx", "anthropic", "claude-haiku-5-5",
     "https://api.anthropic.com/v1/messages", ("x-api-key", "sk-ant-api03-xxxx")),
    ("OPENAI_API_KEY", "sk-proj-xxxx", "openai", "gpt-4o-mini",
     "https://api.openai.com/v1/chat/completions", ("Authorization", "Bearer sk-proj-xxxx")),
    ("GROQ_API_KEY", "gsk_xxxx", "groq", "openai/gpt-oss-20b",
     "https://api.groq.com/openai/v1/chat/completions", ("Authorization", "Bearer gsk_xxxx")),
    ("GOOGLE_API_KEY", "AQ.Ab8xxxx", "google", "gemini-3.6-flash",
     "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
     ("x-goog-api-key", "AQ.Ab8xxxx")),
    ("GOOGLE_API_KEY", "AIzaSyxxxx", "google", "gemini-3.6-flash",
     "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
     ("x-goog-api-key", "AIzaSyxxxx")),
    ("OPENROUTER_API_KEY", "sk-or-v1-xxxx", "openrouter", "openrouter/free",
     "https://openrouter.ai/api/v1/chat/completions", ("Authorization", "Bearer sk-or-v1-xxxx")),
]


@pytest.fixture
def env_vide(monkeypatch):
    for nom in VARIABLES:
        monkeypatch.setattr(settings, nom, None)
    return monkeypatch


def _reponse_simulee(url: str) -> httpx.Response:
    requete = httpx.Request("POST", url)
    if "anthropic" in url:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]}, request=requete)
    if "googleapis" in url:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}, request=requete)
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}, request=requete)


def _capturer(monkeypatch) -> list[dict]:
    appels: list[dict] = []

    def faux_post(url, headers=None, json=None, timeout=None):
        appels.append({"url": url, "headers": headers, "json": json})
        return _reponse_simulee(url)

    monkeypatch.setattr(llm_client.httpx, "post", faux_post)
    return appels


@pytest.mark.parametrize("variable,cle,fournisseur,modele,url,auth", SCENARIOS)
def test_une_seule_cle_suffit(env_vide, variable, cle, fournisseur, modele, url, auth):
    env_vide.setattr(settings, variable, cle)
    appels = _capturer(env_vide)

    assert llm_client.resolve_provider() == (fournisseur, cle, modele)
    assert llm_client.generate_completion("système", "question") == "OK"
    assert appels[0]["url"] == url
    assert appels[0]["headers"][auth[0]] == auth[1]
    if fournisseur != "google":  # Google : le modèle est dans l'URL
        assert appels[0]["json"]["model"] == modele
    assert llm_client.diagnostic_public()["avertissements"] == []


@pytest.mark.parametrize("variable,cle,fournisseur,modele,url,auth", SCENARIOS)
def test_cle_dans_la_variable_generique_llm_api_key(env_vide, variable, cle, fournisseur, modele, url, auth):
    env_vide.setattr(settings, "LLM_API_KEY", cle)
    appels = _capturer(env_vide)
    assert llm_client.resolve_provider() == (fournisseur, cle, modele)
    llm_client.generate_completion("s", "q")
    assert appels[0]["url"] == url


def test_cle_collee_dans_la_mauvaise_variable(env_vide):
    env_vide.setattr(settings, "GOOGLE_API_KEY", "gsk_xxxx")
    assert llm_client.resolve_provider() == ("groq", "gsk_xxxx", "openai/gpt-oss-20b")
    assert "format d'une clé groq" in llm_client.diagnostic_public()["avertissements"][0]


def test_espaces_et_guillemets_retires(env_vide):
    env_vide.setattr(settings, "OPENAI_API_KEY", '  "sk-proj-xxxx"\r\n')
    assert llm_client.resolve_provider() == ("openai", "sk-proj-xxxx", "gpt-4o-mini")


def test_ancien_llm_model_d_un_autre_fournisseur_est_ignore(env_vide):
    # Cas réel : on passe de Gemini à OpenAI en changeant la clé, mais
    # LLM_MODEL=gemini-3.5-flash-lite est resté dans le .env.
    env_vide.setattr(settings, "OPENAI_API_KEY", "sk-proj-xxxx")
    env_vide.setattr(settings, "LLM_MODEL", "gemini-3.5-flash-lite")
    assert llm_client.resolve_provider() == ("openai", "sk-proj-xxxx", "gpt-4o-mini")
    assert "n'est pas un modèle openai" in llm_client.diagnostic_public()["avertissements"][0]


@pytest.mark.parametrize("fournisseur,variable,cle,modele", [
    ("google", "GOOGLE_API_KEY", "AQ.x", "gemini-3.5-flash-lite"),
    ("anthropic", "ANTHROPIC_API_KEY", "sk-ant-x", "claude-sonnet-5-5"),
    ("openai", "OPENAI_API_KEY", "sk-proj-x", "gpt-4.1-mini"),
    ("groq", "GROQ_API_KEY", "gsk_x", "llama-3.3-70b-versatile"),
    ("openrouter", "OPENROUTER_API_KEY", "sk-or-x", "google/gemini-3.6-flash"),
])
def test_llm_model_du_bon_fournisseur_est_respecte(env_vide, fournisseur, variable, cle, modele):
    env_vide.setattr(settings, variable, cle)
    env_vide.setattr(settings, "LLM_MODEL", modele)
    assert llm_client.resolve_provider() == (fournisseur, cle, modele)


def test_plusieurs_cles_priorite_et_avertissement(env_vide):
    env_vide.setattr(settings, "GOOGLE_API_KEY", "AQ.x")
    env_vide.setattr(settings, "OPENAI_API_KEY", "sk-proj-x")
    assert llm_client.resolve_provider()[0] == "openai"
    assert "Plusieurs clés" in llm_client.diagnostic_public()["avertissements"][0]
    env_vide.setattr(settings, "LLM_PROVIDER", " Google ")
    assert llm_client.resolve_provider()[0] == "google"
    assert llm_client.diagnostic_public()["avertissements"] == []


def test_llm_provider_sans_cle_est_signale(env_vide):
    env_vide.setattr(settings, "GROQ_API_KEY", "gsk_x")
    env_vide.setattr(settings, "LLM_PROVIDER", "google")
    assert llm_client.resolve_provider()[0] == "groq"
    assert "aucune clé google" in llm_client.diagnostic_public()["avertissements"][0]


def test_serveur_local_cle_et_modele_libres(env_vide):
    env_vide.setattr(settings, "OPENAI_API_KEY", "ollama")
    env_vide.setattr(settings, "LLM_MODEL", "llama3.1")
    env_vide.setattr(settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    assert llm_client.resolve_provider() == ("openai", "ollama", "llama3.1")
    assert "LLM_BASE_URL" in llm_client.diagnostic_public()["avertissements"][0]


def test_endpoint_fournisseur_ne_renvoie_jamais_la_cle(client, env_vide):
    env_vide.setattr(settings, "GOOGLE_API_KEY", "AQ.secret")
    r = client.get("/api/v1/copilot/fournisseur")
    assert r.status_code == 200
    corps = r.json()
    assert corps["fournisseur"] == "google" and corps["modele"] == "gemini-3.6-flash"
    assert corps["source"] == "GOOGLE_API_KEY"
    assert "AQ.secret" not in r.text


def test_endpoint_fournisseur_sans_cle(client, env_vide):
    r = client.get("/api/v1/copilot/fournisseur")
    assert r.json()["configure"] is False
