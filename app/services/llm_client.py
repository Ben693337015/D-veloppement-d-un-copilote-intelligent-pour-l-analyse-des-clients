"""Client LLM multi-fournisseur (Phase 5) — Anthropic, OpenAI, Groq,
OpenRouter, Google Gemini. Sélection automatique du fournisseur configuré
(cf. `resolve_provider`), pas de dépendance à un SDK propriétaire : un
simple appel HTTP via `httpx` (déjà utilisé ailleurs dans le projet, cf.
requirements.txt) suffit pour chacune de ces API — évite d'alourdir les
dépendances avec 4-5 SDK différents pour une requête de chat complétion
synchrone somme toute simple.

Priorité de résolution du fournisseur (cf. `resolve_provider`) :
  1. `LLM_PROVIDER` explicite, SI la clé API correspondante est configurée.
  2. Sinon, la première clé spécifique trouvée dans l'ordre :
     anthropic -> openai -> groq -> google -> openrouter.
  3. Repli : `LLM_API_KEY` générique (compatibilité ascendante avec
     l'ancien format .env à une seule clé) traité comme une clé Anthropic.
  4. Aucune clé configurée -> None (jamais d'appel réseau tenté).

Aucune fonction ici ne doit jamais laisser fuiter une exception non
capturée vers l'appelant : tout échec (réseau, authentification, réponse
inattendue) est reconverti en `LLMError`, systématiquement interceptée par
`app.services.copilot_service.answer_question` pour ne jamais faire
planter l'endpoint POST /copilot/chat — cf. le bug historique documenté
dans ce fichier (une LLM_API_KEY mal configurée provoquait un 500 brut).
"""

from __future__ import annotations

import httpx

from app.core.config import settings

TIMEOUT_SECONDES = 20.0
MAX_TOKENS_REPONSE = 500

# (nom_attribut_settings, nom_fournisseur, modèle par défaut)
_FOURNISSEURS_PRIORITE: list[tuple[str, str, str]] = [
    ("ANTHROPIC_API_KEY", "anthropic", "claude-3-5-haiku-latest"),
    ("OPENAI_API_KEY", "openai", "gpt-4o-mini"),
    ("GROQ_API_KEY", "groq", "llama-3.1-8b-instant"),
    ("GOOGLE_API_KEY", "google", "gemini-1.5-flash"),
    ("OPENROUTER_API_KEY", "openrouter", "meta-llama/llama-3.1-8b-instruct:free"),
]

_URLS_COMPATIBLES_OPENAI = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


class LLMError(RuntimeError):
    """Toute erreur d'appel LLM (réseau, authentification, réponse
    inattendue) — cf. docstring module : systématiquement interceptée côté
    `copilot_service`, jamais laissée remonter brute jusqu'au client HTTP."""


def resolve_provider() -> tuple[str, str, str] | None:
    """Retourne (fournisseur, cle_api, modele) selon la priorité documentée
    en tête de module, ou None si aucune clé n'est configurée nulle part."""
    if settings.LLM_PROVIDER:
        for env_name, nom, modele_defaut in _FOURNISSEURS_PRIORITE:
            if nom == settings.LLM_PROVIDER:
                cle = getattr(settings, env_name, None)
                if cle:
                    return nom, cle, settings.LLM_MODEL or modele_defaut
                break  # fournisseur demandé sans clé -> repli sur l'auto-sélection ci-dessous

    for env_name, nom, modele_defaut in _FOURNISSEURS_PRIORITE:
        cle = getattr(settings, env_name, None)
        if cle:
            return nom, cle, settings.LLM_MODEL or modele_defaut

    if settings.LLM_API_KEY:
        return "anthropic", settings.LLM_API_KEY, settings.LLM_MODEL or "claude-3-5-haiku-latest"

    return None


def generate_completion(system_prompt: str, user_message: str) -> str:
    """Appelle le fournisseur LLM configuré et retourne le texte généré.
    Lève `LLMError` sur tout échec (jamais d'autre type d'exception)."""
    resolu = resolve_provider()
    if resolu is None:
        raise LLMError("Aucun fournisseur LLM configuré (aucune clé API trouvée).")
    fournisseur, cle_api, modele = resolu

    try:
        if fournisseur == "anthropic":
            return _appel_anthropic(cle_api, modele, system_prompt, user_message)
        if fournisseur == "google":
            return _appel_google(cle_api, modele, system_prompt, user_message)
        # OpenAI, Groq et OpenRouter exposent tous une API "chat completions"
        # compatible OpenAI — un seul appel HTTP paramétré par l'URL de base.
        return _appel_compatible_openai(fournisseur, cle_api, modele, system_prompt, user_message)
    except LLMError:
        raise
    except httpx.HTTPError as exc:
        raise LLMError(f"erreur réseau vers {fournisseur} ({exc})") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"réponse inattendue de {fournisseur} ({exc})") from exc


def _lever_si_erreur_http(reponse: httpx.Response, fournisseur: str) -> None:
    if reponse.status_code >= 400:
        raise LLMError(f"{fournisseur} a répondu {reponse.status_code} ({reponse.text[:200]!r})")


def _appel_anthropic(cle_api: str, modele: str, system_prompt: str, user_message: str) -> str:
    reponse = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": cle_api,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": modele,
            "max_tokens": MAX_TOKENS_REPONSE,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        },
        timeout=TIMEOUT_SECONDES,
    )
    _lever_si_erreur_http(reponse, "anthropic")
    data = reponse.json()
    blocs_texte = [b["text"] for b in data["content"] if b.get("type") == "text"]
    if not blocs_texte:
        raise LLMError("réponse Anthropic sans contenu texte")
    return "\n".join(blocs_texte).strip()


def _appel_compatible_openai(
    fournisseur: str, cle_api: str, modele: str, system_prompt: str, user_message: str
) -> str:
    reponse = httpx.post(
        _URLS_COMPATIBLES_OPENAI[fournisseur],
        headers={"Authorization": f"Bearer {cle_api}", "content-type": "application/json"},
        json={
            "model": modele,
            "max_tokens": MAX_TOKENS_REPONSE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=TIMEOUT_SECONDES,
    )
    _lever_si_erreur_http(reponse, fournisseur)
    data = reponse.json()
    contenu = data["choices"][0]["message"]["content"]
    if not contenu:
        raise LLMError(f"réponse {fournisseur} sans contenu")
    return contenu.strip()


def _appel_google(cle_api: str, modele: str, system_prompt: str, user_message: str) -> str:
    reponse = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent",
        params={"key": cle_api},
        headers={"content-type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": MAX_TOKENS_REPONSE},
        },
        timeout=TIMEOUT_SECONDES,
    )
    _lever_si_erreur_http(reponse, "google")
    data = reponse.json()
    parties = data["candidates"][0]["content"]["parts"]
    texte = "".join(p.get("text", "") for p in parties)
    if not texte:
        raise LLMError("réponse Google sans contenu texte")
    return texte.strip()
