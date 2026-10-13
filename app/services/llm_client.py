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

import json

import httpx

from app.core.config import settings

TIMEOUT_SECONDES = 20.0
MAX_TOKENS_REPONSE = 500

# (nom_attribut_settings, nom_fournisseur, modèle par défaut)
_FOURNISSEURS_PRIORITE: list[tuple[str, str, str]] = [
    ("ANTHROPIC_API_KEY", "anthropic", "claude-3-5-haiku-latest"),
    ("OPENAI_API_KEY", "openai", "gpt-4o-mini"),
    # llama-3.1-8b-instant a été définitivement retiré par Groq le
    # 16/08/2026 (annonce du 17/06/2026) ; openai/gpt-oss-20b est le
    # remplacement recommandé par Groq lui-même (cf. console.groq.com/docs/deprecations).
    ("GROQ_API_KEY", "groq", "openai/gpt-oss-20b"),
    ("GOOGLE_API_KEY", "google", "gemini-1.5-flash"),
    ("OPENROUTER_API_KEY", "openrouter", "meta-llama/llama-3.1-8b-instruct:free"),
]

# Tâche #6 de la roadmap de clôture — fournisseurs pour lesquels le
# tool-calling est implémenté ci-dessous. Volontairement limité à
# Anthropic dans ce périmètre (le fournisseur prioritaire de la liste
# ci-dessus) : les autres fournisseurs restent utilisables pour le chat
# SANS outils (`generate_completion`), et `copilot_service` (tâche #7)
# doit vérifier `supports_tool_calling(fournisseur)` avant d'appeler
# `generate_completion_with_tools` — sinon repli sur le mode local, jamais
# d'exception laissée remonter pour un fournisseur non supporté.
_FOURNISSEURS_TOOL_CALLING = {"anthropic", "groq"}

# Le modèle par défaut de _FOURNISSEURS_PRIORITE pour Groq
# (openai/gpt-oss-20b) privilégie la vitesse, pas la fiabilité du tool
# use agentique. On force un modèle plus grand, documenté par Groq
# lui-même comme fiable pour le function calling (utilisé par leur
# propre système "Compound", cf. console.groq.com/docs/compound),
# UNIQUEMENT pour le mode agent — le chat simple (`generate_completion`)
# garde le modèle rapide par défaut.
_MODELE_TOOL_CALLING_PAR_FOURNISSEUR = {"groq": "openai/gpt-oss-120b"}
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

def supports_tool_calling(fournisseur: str) -> bool:
    """Tâche #6 — `copilot_service` (tâche #7) appelle ceci AVANT
    `generate_completion_with_tools` pour décider s'il peut tenter le
    tool-calling ou doit rester sur `generate_completion` (chat simple)."""
    return fournisseur in _FOURNISSEURS_TOOL_CALLING


def generate_completion_with_tools(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """Tâche #6 — variante de `generate_completion` qui transmet la liste
    d'outils au fournisseur et restitue la réponse SANS l'interpréter :
    exécuter l'outil demandé et boucler est la responsabilité de
    `copilot_service` (tâche #7), pas de ce module.

    `messages` : historique au format Anthropic — `[{"role": "user"|"assistant",
    "content": str | list[bloc]}]`. Pour renvoyer un résultat d'outil,
    ajouter un message `{"role": "user", "content": [{"type": "tool_result",
    "tool_use_id": ..., "content": <résultat en texte>}]}` (cf. tâche #7).

    `tools` : `[{"name": str, "description": str, "input_schema": <JSON Schema>}]`
    (format Anthropic natif — c'est le seul fournisseur supporté ici, cf.
    `_FOURNISSEURS_TOOL_CALLING`).

    Retourne `{"stop_reason": "tool_use" | "end_turn" | ..., "text": str |
    None, "tool_calls": [{"id": str, "name": str, "input": dict}],
    "assistant_content": list[dict]}` — `assistant_content` est le bloc
    `content` brut renvoyé par Anthropic, à repasser TEL QUEL dans le
    prochain message `{"role": "assistant", "content": assistant_content}`
    de l'historique (exigence de l'API Anthropic : le tour assistant
    contenant une demande d'outil doit être rejoué identique avant le
    `tool_result` qui y répond).

    Lève `LLMError` si le fournisseur résolu ne supporte pas le
    tool-calling — l'appelant doit avoir vérifié `supports_tool_calling`
    avant, ceci est un garde-fou de dernier recours, pas le mécanisme de
    repli principal."""
    resolu = resolve_provider()
    if resolu is None:
        raise LLMError("Aucun fournisseur LLM configuré (aucune clé API trouvée).")
    fournisseur, cle_api, modele = resolu

    if fournisseur not in _FOURNISSEURS_TOOL_CALLING:
        raise LLMError(
            f"le fournisseur '{fournisseur}' ne supporte pas le tool-calling dans cette "
            "intégration — vérifier supports_tool_calling() avant d'appeler cette fonction."
        )

    modele_effectif = _MODELE_TOOL_CALLING_PAR_FOURNISSEUR.get(fournisseur, modele)

    try:
        return _appel_avec_retry_reseau(fournisseur, cle_api, modele_effectif, system_prompt, messages, tools)
    except LLMError:
        raise
    except httpx.HTTPError as exc:
        raise LLMError(f"erreur réseau vers {fournisseur} ({exc})") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"réponse inattendue de {fournisseur} ({exc})") from exc


def _appel_avec_retry_reseau(
    fournisseur: str, cle_api: str, modele: str, system_prompt: str, messages: list[dict], tools: list[dict]
) -> dict:
    """Une seule nouvelle tentative en cas de coupure réseau TRANSITOIRE
    (ex. 'Server disconnected without sending a response', observé en
    conditions réelles avec Groq) — jamais de retry sur un 4xx/5xx explicite
    du fournisseur (déjà converti en `LLMError` par `_lever_si_erreur_http`,
    donc jamais intercepté ici) : retenter immédiatement contre un vrai 429
    (quota tokens/minute dépassé) échouerait très probablement de nouveau
    et gaspillerait un appel supplémentaire contre ce même quota."""
    appel = _appel_anthropic_avec_outils if fournisseur == "anthropic" else _appel_groq_avec_outils
    try:
        return appel(cle_api, modele, system_prompt, messages, tools)
    except httpx.HTTPError:
        return appel(cle_api, modele, system_prompt, messages, tools)
    
def _appel_anthropic_avec_outils(
    cle_api: str, modele: str, system_prompt: str, messages: list[dict], tools: list[dict]
) -> dict:
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
            "messages": messages,
            "tools": tools,
        },
        timeout=TIMEOUT_SECONDES,
    )
    _lever_si_erreur_http(reponse, "anthropic")
    data = reponse.json()
    contenu = data["content"]

    blocs_texte = [b["text"] for b in contenu if b.get("type") == "text"]
    appels_outils = [
        {"id": b["id"], "name": b["name"], "input": b["input"]}
        for b in contenu
        if b.get("type") == "tool_use"
    ]

    return {
        "stop_reason": data.get("stop_reason"),
        "text": "\n".join(blocs_texte).strip() if blocs_texte else None,
        "tool_calls": appels_outils,
        "assistant_content": contenu,
    }

# --- Groq (API compatible OpenAI, format tool-calling différent d'Anthropic) ---
#
# Le protocole interne de `messages`/`tools` échangé avec `copilot_service`
# reste au format Anthropic partout (déjà écrit, testé — tâches #6/#7) :
# les deux fonctions ci-dessous font toute la traduction aller-retour vers
# le format OpenAI-compatible attendu par Groq, en frontière de ce module.
# `copilot_service.py` ne sait pas quel fournisseur répond et n'a AUCUNE
# modification à faire pour gérer un second fournisseur de tool-calling.


def _tools_anthropic_vers_openai(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in tools
    ]


def _messages_anthropic_vers_openai(messages: list[dict]) -> list[dict]:
    convertis = []
    for msg in messages:
        contenu = msg["content"]
        if isinstance(contenu, str):
            convertis.append({"role": msg["role"], "content": contenu})
            continue
        if msg["role"] == "assistant":
            texte = None
            tool_calls = []
            for bloc in contenu:
                if bloc["type"] == "text":
                    texte = bloc["text"]
                elif bloc["type"] == "tool_use":
                    tool_calls.append(
                        {
                            "id": bloc["id"],
                            "type": "function",
                            "function": {"name": bloc["name"], "arguments": json.dumps(bloc["input"], ensure_ascii=False)},
                        }
                    )
            message_openai = {"role": "assistant", "content": texte}
            if tool_calls:
                message_openai["tool_calls"] = tool_calls
            convertis.append(message_openai)
        else:
            # Message utilisateur portant un ou plusieurs tool_result
            # (format Anthropic) -> chaque tool_result devient son PROPRE
            # message top-level {"role": "tool", ...} en format OpenAI.
            for bloc in contenu:
                convertis.append({"role": "tool", "tool_call_id": bloc["tool_use_id"], "content": bloc["content"]})
    return convertis


def _appel_groq_avec_outils(
    cle_api: str, modele: str, system_prompt: str, messages: list[dict], tools: list[dict]
) -> dict:
    payload_messages = [{"role": "system", "content": system_prompt}] + _messages_anthropic_vers_openai(messages)

    reponse = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {cle_api}", "Content-Type": "application/json"},
        json={
            "model": modele,
            "messages": payload_messages,
            "tools": _tools_anthropic_vers_openai(tools),
            "tool_choice": "auto",
        },
        timeout=TIMEOUT_SECONDES,
    )
    _lever_si_erreur_http(reponse, "groq")
    data = reponse.json()
    message = data["choices"][0]["message"]
    finish_reason = data["choices"][0]["finish_reason"]

    tool_calls_bruts = message.get("tool_calls") or []
    appels_outils = [
        {"id": tc["id"], "name": tc["function"]["name"], "input": json.loads(tc["function"]["arguments"])}
        for tc in tool_calls_bruts
    ]

    blocs_assistant = []
    if message.get("content"):
        blocs_assistant.append({"type": "text", "text": message["content"]})
    for appel in appels_outils:
        blocs_assistant.append({"type": "tool_use", "id": appel["id"], "name": appel["name"], "input": appel["input"]})

    return {
        "stop_reason": "tool_use" if finish_reason == "tool_calls" else finish_reason,
        "text": message.get("content"),
        "tool_calls": appels_outils,
        "assistant_content": blocs_assistant,
    }

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
