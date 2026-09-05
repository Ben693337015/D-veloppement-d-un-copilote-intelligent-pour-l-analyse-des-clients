"""Service métier — assistant conversationnel RAG mutualisé (Module D, Cadrage §CDCF 3.4).

Contexte injecté dans le prompt : synthèse texte des indicateurs réels
(KPIs, alertes stock/trésorerie, segments RFM) générée par
`build_context_snapshot` — une forme légère de RAG "context-injection"
plutôt qu'un vrai index vectoriel ChromaDB (non présent dans ce projet
malgré une mention dans la vision initiale — cf. requirements.txt : pas de
dépendance chromadb). Suffisant tant que le contexte tient dans la fenêtre
du modèle (quelques KPIs + segments, largement le cas ici) ; un index
vectoriel deviendrait pertinent si le contexte grossissait significativement
(historique complet de transactions, par exemple) — piste explicitement
hors périmètre de cette itération.

Le LLM est appelé via `app.services.llm_client` (multi-fournisseur :
Anthropic, OpenAI, Groq, Google, OpenRouter — cf. ce module pour l'ordre de
priorité). Sans clé configurée, retombe sur le mode local (résumé factuel).
"""

from sqlalchemy.orm import Session

from app.services import analytics_service, llm_client, marketing_service

SYSTEM_PROMPT = (
    "Tu es le copilote IA d'une plateforme d'analyse pour PME (projet académique "
    "Groupe 2, Master IA Appliquée, Université de Ngaoundéré). Réponds en français, "
    "de façon concise et factuelle, en t'appuyant STRICTEMENT sur le contexte fourni "
    "ci-dessous (indicateurs réels de l'entreprise). N'invente jamais un chiffre "
    "absent du contexte ; si l'information demandée n'y figure pas, dis-le "
    "clairement plutôt que de deviner. Le contexte peut inclure un segment "
    "\"Atypique (B2B/grossiste)\" (clients isolés par DBSCAN, à traiter hors "
    "campagnes marketing grand public)."
)


def build_context_snapshot(db: Session) -> str:
    """Construit un contexte texte court à injecter dans le prompt RAG."""
    kpis = analytics_service.get_kpis_globaux(db)
    segments = marketing_service.get_segment_summary(db)
    stock_alertes = analytics_service.get_stock_alertes(db)

    lignes = [
        f"CA total: {kpis['chiffre_affaires_total']:.2f}",
        f"Clients: {kpis['nb_clients']} | Transactions: {kpis['nb_transactions']}",
        f"Solde trésorerie estimé: {kpis['solde_tresorerie_estime']:.2f}",
        f"Alertes stock actives: {len(stock_alertes)}",
    ]
    for seg in segments:
        lignes.append(
            f"Segment {seg['segment_rfm']}: {seg['nb_clients']} clients, "
            f"churn moyen {seg['score_churn_moyen']}"
        )
    return "\n".join(lignes)


def answer_question(db: Session, question: str) -> dict:
    """Point d'entrée de /api/v1/copilot/chat.

    Sans fournisseur LLM configuré (cf. `llm_client.resolve_provider`),
    retourne un résumé factuel basé sur le contexte plutôt qu'une
    génération libre, pour que l'API reste utilisable en local sans
    dépendance externe.

    Si un fournisseur EST configuré, `llm_client.generate_completion` est
    appelé avec le contexte réel injecté dans le prompt système. Tout échec
    (réseau, authentification, quota, réponse inattendue) est intercepté
    (`llm_client.LLMError`) et bascule sur le même mode local — JAMAIS de
    500 brut, quelle que soit la qualité de la configuration LLM (bug déjà
    corrigé une première fois pour l'ancien stub, principe conservé pour
    l'intégration réelle : une clé mal configurée ne doit jamais casser
    l'endpoint pour l'utilisateur final)."""
    contexte = build_context_snapshot(db)
    resolu = llm_client.resolve_provider()

    if resolu is None:
        return {
            "reponse": (
                "Mode local (sans LLM configuré) — voici la synthèse des "
                f"indicateurs disponibles pour répondre à : « {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service"],
            "explicabilite": [],
        }

    fournisseur, _cle_api, _modele = resolu
    prompt_utilisateur = f"Contexte (indicateurs réels de l'entreprise) :\n{contexte}\n\nQuestion : {question}"

    try:
        reponse_llm = llm_client.generate_completion(SYSTEM_PROMPT, prompt_utilisateur)
        return {
            "reponse": reponse_llm,
            "sources": ["analytics_service", "marketing_service", f"llm:{fournisseur}"],
            "explicabilite": [],
        }
    except llm_client.LLMError as exc:
        return {
            "reponse": (
                f"Le fournisseur LLM configuré ({fournisseur}) n'a pas répondu correctement "
                f"({exc}) — voici en attendant la synthèse factuelle disponible pour répondre "
                f"à : « {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service"],
            "explicabilite": [],
        }
