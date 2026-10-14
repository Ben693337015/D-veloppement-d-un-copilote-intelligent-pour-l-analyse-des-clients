"""Client LLM multi-fournisseur (Phase 5) — Anthropic, OpenAI, Groq,
OpenRouter, Google Gemini. Sélection automatique du fournisseur configuré
(cf. `resolve_provider`), pas de dépendance à un SDK propriétaire : un
simple appel HTTP via `httpx` (déjà utilisé ailleurs dans le projet, cf.
requirements.txt) suffit pour chacune de ces API — évite d'alourdir les
dépendances avec 4-5 SDK différents pour une requête de chat complétion
synchrone somme toute simple.

Changer de fournisseur = changer de clé (cf. `diagnostic`, 14/10/2026) :
  1. Le fournisseur est reconnu au préfixe de la clé (sk-ant-, sk-or-, gsk_,
     AIza/AQ., sk-), quelle que soit la variable où elle est collée, y
     compris la variable générique `LLM_API_KEY`.
  2. Plusieurs clés : `LLM_PROVIDER` s'il a une clé, sinon l'ordre
     anthropic -> openai -> groq -> google -> openrouter.
  3. `LLM_MODEL` n'est appliqué que s'il appartient au fournisseur retenu ;
     sinon le modèle par défaut de ce fournisseur est utilisé.
  4. Aucune clé -> None (jamais d'appel réseau tenté).
Les écarts (clé mal placée, LLM_MODEL ignoré, plusieurs clés…) sont
signalés dans les avertissements de GET /copilot/fournisseur et au démarrage.

Aucune fonction ici ne doit jamais laisser fuiter une exception non
capturée vers l'appelant : tout échec (réseau, authentification, réponse
inattendue) est reconverti en `LLMError`, systématiquement interceptée par
`app.services.copilot_service.answer_question` pour ne jamais faire
planter l'endpoint POST /copilot/chat — cf. le bug historique documenté
dans ce fichier (une LLM_API_KEY mal configurée provoquait un 500 brut).
"""

from __future__ import annotations

import threading
import time

import httpx

from app.core.config import settings

TIMEOUT_SECONDES = 30.0
# CORRIGÉ (octobre 2026) : 500 -> 1500. Les modèles récents (Gemini 3.x,
# gpt-oss sur Groq) « réfléchissent » avant de répondre et ces jetons de
# raisonnement sont décomptés du même plafond : avec 500, la réponse visible
# pouvait être vide ou tronquée. Le plafond n'allonge pas les réponses (le
# prompt système demande la concision), il évite seulement la coupure.
MAX_TOKENS_REPONSE = 1500

# (nom_attribut_settings, nom_fournisseur, modèle par défaut)
# CORRIGÉ (octobre 2026) : les anciens défauts avaient été retirés par les
# fournisseurs -> HTTP 404 et bascule systématique en mode secours :
#   claude-3-5-haiku  retiré le 19/02/2026  -> claude-haiku-5-5
#   llama-3.1-8b-instant (Groq) retiré le 16/08/2026 -> openai/gpt-oss-20b
#   gemini-1.5-flash  retiré (404 sur v1beta) -> gemini-3.6-flash
#   meta-llama/...:free (OpenRouter) -> routeur « openrouter/free »
# Sources : pages « deprecations » officielles de chaque fournisseur.
# Les modèles évoluent vite : LLM_MODEL (.env) permet d'en forcer un autre
# sans toucher au code.
MODELE_ANTHROPIC_DEFAUT = "claude-haiku-5-5"
_FOURNISSEURS_PRIORITE: list[tuple[str, str, str]] = [
    ("ANTHROPIC_API_KEY", "anthropic", MODELE_ANTHROPIC_DEFAUT),
    ("OPENAI_API_KEY", "openai", "gpt-4o-mini"),
    ("GROQ_API_KEY", "groq", "openai/gpt-oss-20b"),
    ("GOOGLE_API_KEY", "google", "gemini-3.6-flash"),
    ("OPENROUTER_API_KEY", "openrouter", "openrouter/free"),
]

_URLS_COMPATIBLES_OPENAI = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


_URLS_BASE_OFFICIELLES = {
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
}


def _url(fournisseur: str, suffixe: str) -> str:
    """URL d'appel : `settings.LLM_BASE_URL` si défini (LLM local type
    Ollama/LM Studio, proxy, serveur simulé), sinon l'URL officielle."""
    base = (settings.LLM_BASE_URL or "").strip().strip('"').strip("'").strip()
    if base:
        return base.rstrip("/") + suffixe
    if fournisseur in _URLS_COMPATIBLES_OPENAI:
        return _URLS_COMPATIBLES_OPENAI[fournisseur]
    return _URLS_BASE_OFFICIELLES[fournisseur] + suffixe


class LLMError(RuntimeError):
    """Toute erreur d'appel LLM (réseau, authentification, réponse
    inattendue) — cf. docstring module : systématiquement interceptée côté
    `copilot_service`, jamais laissée remonter brute jusqu'au client HTTP."""


class LLMSurcharge(LLMError):
    """Erreur TEMPORAIRE du fournisseur (429 quota/débit, 500, 502, 503,
    504) : la même requête a de bonnes chances de passer un peu plus tard ou
    avec un autre modèle. Cf. `generate_completion` (nouvelles tentatives)."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# AJOUT (14/10/2026) — gestion des surcharges (« This model is currently
# experiencing high demand », HTTP 503 de Gemini, constaté en usage réel) :
#   1. jusqu'à NOUVELLES_TENTATIVES nouvelles tentatives sur le même modèle,
#      avec une attente croissante (ou celle indiquée par Retry-After) ;
#   2. puis bascule sur un modèle de secours du MÊME fournisseur (même clé),
#      en général moins demandé ;
#   3. seulement ensuite, LLMError -> synthèse de secours côté copilote.
NOUVELLES_TENTATIVES = 2
ATTENTES_SECONDES = (1.5, 3.0)
ATTENTE_MAX_SECONDES = 5.0
MODELES_SECOURS: dict[str, list[str]] = {
    "google": ["gemini-3.5-flash-lite"],
    "anthropic": ["claude-sonnet-5-5"],
    "openai": [],
    "groq": [],
    "openrouter": [],
}
_local = threading.local()


def dernier_modele_utilise() -> str | None:
    """Modèle qui a réellement répondu au dernier appel de ce thread (peut
    être un modèle de secours) — affiché sous la réponse du copilote."""
    return getattr(_local, "modele", None)


# AJOUT (14/10/2026) — « changer de fournisseur en changeant seulement la
# clé API ». Les préfixes des clés sont publics et stables ; ils permettent de
# reconnaître le fournisseur même si la clé est collée dans la mauvaise
# variable (ex. une clé Groq « gsk_… » dans GOOGLE_API_KEY) ou dans la
# variable générique LLM_API_KEY. Ordre : du plus spécifique au plus général
# (« sk-ant- » et « sk-or- » avant « sk- »).
_PREFIXES_CLES: list[tuple[str, str]] = [
    ("sk-ant-", "anthropic"),
    ("sk-or-", "openrouter"),
    ("gsk_", "groq"),
    ("AIza", "google"),
    ("AQ.", "google"),
    ("sk-", "openai"),
]
FOURNISSEURS = [nom for _env, nom, _m in _FOURNISSEURS_PRIORITE]
_MODELES_DEFAUT = {nom: m for _env, nom, m in _FOURNISSEURS_PRIORITE}


def _nettoyer(valeur: str | None) -> str | None:
    """Retire espaces, retours chariot et guillemets autour d'une valeur du
    .env (erreur fréquente au copier-coller d'une clé) ; vide -> None."""
    if valeur is None:
        return None
    valeur = valeur.strip().strip('"').strip("'").strip()
    return valeur or None


def detecter_fournisseur_cle(cle: str | None) -> str | None:
    """Fournisseur reconnu d'après le préfixe de la clé, ou None (clé de
    serveur local, de test, ou format inconnu)."""
    cle = _nettoyer(cle)
    if not cle:
        return None
    for prefixe, nom in _PREFIXES_CLES:
        if cle.startswith(prefixe):
            return nom
    return None


def modele_compatible(fournisseur: str, modele: str) -> bool:
    """Le modèle demandé (LLM_MODEL) peut-il être servi par ce fournisseur ?
    Évite l'erreur classique : on remplace la clé Google par une clé OpenAI
    mais LLM_MODEL=gemini-… reste dans le .env -> 404 systématique.
    Avec LLM_BASE_URL (Ollama, proxy…), le nom du modèle est libre."""
    if _nettoyer(settings.LLM_BASE_URL):
        return True
    m = modele.lower().removeprefix("models/")
    if fournisseur == "anthropic":
        return m.startswith("claude")
    if fournisseur == "google":
        return m.startswith(("gemini", "gemma", "learnlm"))
    if fournisseur == "openrouter":
        return "/" in m  # identifiants OpenRouter : « éditeur/modèle »
    if fournisseur == "openai":
        return "/" not in m and not m.startswith(("claude", "gemini", "gemma", "llama", "mixtral", "qwen", "deepseek"))
    if fournisseur == "groq":
        return not m.startswith(("claude", "gemini", "gpt-", "o1", "o3", "o4", "chatgpt"))
    return True


def diagnostic() -> dict:
    """Configuration LLM effectivement retenue + avertissements, sans jamais
    exposer la clé. Utilisé par resolve_provider, au démarrage (journal) et
    par GET /copilot/fournisseur."""
    avertissements: list[str] = []
    base_url = _nettoyer(settings.LLM_BASE_URL)

    # 1. Clés présentes -> (fournisseur, clé, variable source), une par fournisseur.
    candidats: list[tuple[str, str, str]] = []
    sources = [(env, nom) for env, nom, _m in _FOURNISSEURS_PRIORITE] + [("LLM_API_KEY", None)]
    for env_name, nom_variable in sources:
        cle = _nettoyer(getattr(settings, env_name, None))
        if not cle:
            continue
        detecte = None if base_url else detecter_fournisseur_cle(cle)
        if nom_variable is None:  # LLM_API_KEY générique
            fournisseur = detecte or "anthropic"
        elif detecte and detecte != nom_variable:
            fournisseur = detecte
            avertissements.append(
                f"La clé de {env_name} a le format d'une clé {detecte} : elle est utilisée avec {detecte}."
            )
        else:
            fournisseur = nom_variable
        if all(c[0] != fournisseur for c in candidats):
            candidats.append((fournisseur, cle, env_name))

    if not candidats:
        return {"configure": False, "fournisseur": None, "modele": None, "source": None,
                "url": None, "avertissements": avertissements}

    # 2. Choix du fournisseur : LLM_PROVIDER s'il a une clé, sinon le premier.
    choisi = candidats[0]
    demande = (_nettoyer(settings.LLM_PROVIDER) or "").lower() or None
    if demande:
        if demande not in FOURNISSEURS:
            avertissements.append(
                f"LLM_PROVIDER={demande} inconnu (valeurs possibles : {', '.join(FOURNISSEURS)}) : ignoré."
            )
        else:
            trouve = next((c for c in candidats if c[0] == demande), None)
            if trouve:
                choisi = trouve
            else:
                avertissements.append(
                    f"LLM_PROVIDER={demande} mais aucune clé {demande} n'est renseignée : "
                    f"{choisi[0]} est utilisé à la place."
                )
    if len(candidats) > 1 and not (demande and choisi[0] == demande):
        autres = ", ".join(c[2] for c in candidats if c is not choisi)
        avertissements.append(
            f"Plusieurs clés sont renseignées ; {choisi[0]} ({choisi[2]}) est utilisé par priorité. "
            f"Videz les autres ({autres}) ou fixez LLM_PROVIDER pour en choisir une autre."
        )
    fournisseur, cle, source = choisi

    # 3. Modèle : LLM_MODEL seulement s'il appartient à ce fournisseur.
    modele = _MODELES_DEFAUT[fournisseur]
    demande_modele = _nettoyer(settings.LLM_MODEL)
    if demande_modele:
        if modele_compatible(fournisseur, demande_modele):
            modele = demande_modele
        else:
            avertissements.append(
                f"LLM_MODEL={demande_modele} n'est pas un modèle {fournisseur} : "
                f"modèle par défaut {modele} utilisé (videz LLM_MODEL ou mettez un modèle {fournisseur})."
            )
    if base_url:
        avertissements.append(
            f"LLM_BASE_URL={base_url} est actif : les appels partent vers cette adresse et non vers "
            f"l'API officielle {fournisseur}. Videz-le pour utiliser l'API officielle."
        )

    return {"configure": True, "fournisseur": fournisseur, "modele": modele, "source": source,
            "url": _url(fournisseur, "/messages" if fournisseur == "anthropic" else
                        f"/models/{modele}:generateContent" if fournisseur == "google" else "/chat/completions"),
            "avertissements": avertissements, "_cle": cle}


def diagnostic_public() -> dict:
    """`diagnostic()` sans la clé (pour le journal et l'API)."""
    d = diagnostic()
    d.pop("_cle", None)
    return d


def resolve_provider() -> tuple[str, str, str] | None:
    """Retourne (fournisseur, cle_api, modele), ou None si aucune clé n'est
    configurée. Règles (cf. `diagnostic`) :
      - une seule clé renseignée -> ce fournisseur, quelle que soit la
        variable où elle est collée (préfixe reconnu) ;
      - plusieurs clés -> LLM_PROVIDER, sinon la priorité
        anthropic -> openai -> groq -> google -> openrouter ;
      - LLM_MODEL n'est appliqué que s'il correspond au fournisseur retenu."""
    d = diagnostic()
    if not d["configure"]:
        return None
    return d["fournisseur"], d["_cle"], d["modele"]


def _appel_unique(fournisseur: str, cle_api: str, modele: str, system_prompt: str, user_message: str) -> str:
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
    except httpx.TimeoutException as exc:
        raise LLMError(f"{fournisseur} n'a pas répondu dans les {TIMEOUT_SECONDES:.0f} s") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"erreur réseau vers {fournisseur} ({exc})") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"réponse inattendue de {fournisseur} ({exc})") from exc


def generate_completion(system_prompt: str, user_message: str) -> str:
    """Appelle le fournisseur LLM configuré et retourne le texte généré.
    Sur surcharge temporaire, réessaie puis bascule sur un modèle de secours
    (cf. MODELES_SECOURS). Lève `LLMError` sur tout échec définitif (jamais
    d'autre type d'exception)."""
    resolu = resolve_provider()
    if resolu is None:
        raise LLMError("Aucun fournisseur LLM configuré (aucune clé API trouvée).")
    fournisseur, cle_api, modele = resolu
    modeles = [modele] + [m for m in MODELES_SECOURS.get(fournisseur, []) if m != modele]

    derniere_surcharge: LLMSurcharge | None = None
    for modele_essaye in modeles:
        for tentative in range(NOUVELLES_TENTATIVES + 1):
            try:
                texte = _appel_unique(fournisseur, cle_api, modele_essaye, system_prompt, user_message)
                _local.modele = modele_essaye
                return texte
            except LLMSurcharge as exc:
                derniere_surcharge = exc
                if tentative < NOUVELLES_TENTATIVES:
                    attente = exc.retry_after or ATTENTES_SECONDES[min(tentative, len(ATTENTES_SECONDES) - 1)]
                    time.sleep(min(attente, ATTENTE_MAX_SECONDES))
    raise LLMError(
        f"{fournisseur} est momentanément surchargé ({', '.join(modeles)} essayés, "
        f"{NOUVELLES_TENTATIVES + 1} tentatives chacun) : réessayez dans quelques instants. "
        f"Détail : {derniere_surcharge}"
    )


def _lever_si_erreur_http(reponse: httpx.Response, fournisseur: str) -> None:
    if reponse.status_code == 404:
        # Cas le plus fréquent en pratique : modèle retiré par le fournisseur.
        raise LLMError(
            f"{fournisseur} a répondu 404 : modèle introuvable ou retiré — "
            "renseigner un modèle actuel dans LLM_MODEL (.env) puis redémarrer l'API "
            f"({reponse.text[:150]!r})"
        )
    if reponse.status_code in (401, 403):
        raise LLMError(
            f"{fournisseur} a refusé la clé API ({reponse.status_code}) : vérifier la clé dans .env "
            "(sans espace ni guillemets), qu'elle est autorisée pour cette API, puis redémarrer l'API "
            f"({reponse.text[:150]!r})"
        )
    if reponse.status_code in (429, 500, 502, 503, 504):
        try:
            retry_after = float(reponse.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise LLMSurcharge(
            f"{fournisseur} a répondu {reponse.status_code} (surcharge ou quota temporaire) "
            f"({reponse.text[:150]!r})",
            retry_after=retry_after,
        )
    if reponse.status_code >= 400:
        raise LLMError(f"{fournisseur} a répondu {reponse.status_code} ({reponse.text[:200]!r})")


def _appel_anthropic(cle_api: str, modele: str, system_prompt: str, user_message: str) -> str:
    reponse = httpx.post(
        _url("anthropic", "/messages"),
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
        _url(fournisseur, "/chat/completions"),
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
        _url("google", f"/models/{modele}:generateContent"),
        # CORRIGÉ (13/10/2026) : la clé passait dans l'URL (?key=). Google AI
        # Studio ne génère plus que des clés d'authentification « AQ. », et la
        # documentation officielle ne montre que l'en-tête x-goog-api-key.
        # L'en-tête accepte aussi les anciennes clés « AIza », et la clé
        # n'apparaît plus dans les journaux d'URL.
        headers={"x-goog-api-key": cle_api, "content-type": "application/json"},
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
