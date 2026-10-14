"""Accès du copilote à la base de données (texte → SQL, lecture seule).

Principe (agent « text-to-SQL » en 3 temps, cf. copilot_service) :
  1. le LLM reçoit le SCHÉMA documenté ci-dessous + la question, et écrit
     UNE requête SELECT (ou répond AUCUNE si la question n'en a pas besoin) ;
  2. l'application VALIDE la requête (`valider_requete`) puis l'EXÉCUTE en
     lecture seule (`executer_requete`) ; en cas de refus ou d'erreur SQL, le
     LLM reçoit le message d'erreur et a UNE chance de corriger sa requête ;
  3. le LLM rédige la réponse à partir des lignes réellement renvoyées.

Pourquoi du texte → SQL plutôt que des « outils » (function calling) : les 5
fournisseurs supportés (Anthropic, OpenAI, Groq, Google, OpenRouter) ont des
formats d'appel d'outils différents, alors qu'une simple complétion de texte
fonctionne partout (et avec un LLM local via LLM_BASE_URL). Une seule
requête SQL couvre aussi des questions imprévues (« CA par pays en 2013 »,
« produits jamais vendus »…) qu'un catalogue d'outils fixe ne couvrirait pas.

Sécurité — défense en profondeur, chaque couche suffit seule à bloquer une
écriture :
  a. ANALYSE SYNTAXIQUE (sqlglot) : une seule instruction, de type requête
     (SELECT / WITH / UNION) ; aucun nœud d'écriture ou de DDL dans l'arbre ;
     tables limitées à la liste blanche (la table `users` et les schémas
     système sont exclus) ; colonnes sensibles interdites (email, hash du mot
     de passe ; e-mail sauf COPILOTE_ACCES_EMAILS=true) ; `SELECT *` interdit (il exposerait ces colonnes) ; fonctions
     système interdites (pg_sleep, pg_read_file, dblink, set_config…).
  b. EXÉCUTION : transaction `READ ONLY` + `statement_timeout` (PostgreSQL)
     ou `PRAGMA query_only` (SQLite, tests) ; annulée (ROLLBACK) à la fin.
  c. Optionnel : `COPILOTE_DATABASE_URL_LECTURE` = connexion avec un rôle
     PostgreSQL qui n'a que le droit SELECT (cf. scripts/init_db.sql).
  d. Résultat borné (`LIGNES_MAX` lignes) pour ne pas saturer le prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import sqlglot
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlglot import exp

from app.core.config import settings

LIGNES_MAX = 200  # lignes lues au maximum par requête
LIGNES_DANS_LE_PROMPT = 50  # lignes montrées au LLM pour rédiger la réponse
TIMEOUT_REQUETE_MS = 5000

# Schéma exposé au LLM : SEULES ces tables et colonnes existent pour lui.
# Les descriptions servent au LLM à écrire une requête correcte du premier
# coup (unités, conventions comme « retours exclus du CA »).
SCHEMA_EXPOSE: dict[str, dict[str, str]] = {
    "clients": {
        "client_id": "uuid, clé primaire",
        "code_client_externe": "identifiant du client dans le fichier importé",
        "nom": "nom du client",
        "pays": "pays du client",
        "recence_jours": "jours depuis le dernier achat (calculé par la segmentation RFM)",
        "frequence_achats": "nombre d'achats distincts dans la fenêtre d'analyse RFM",
        "montant_total": "montant dépensé dans la fenêtre d'analyse RFM",
        "score_r": "score de récence 1-5 (5 = achat très récent)",
        "score_f": "score de fréquence 1-5",
        "score_m": "score de montant 1-5",
        "segment_rfm": "segment : Fidèle, Occasionnel, Nouveau, À risque, Perdu, Atypique (B2B/grossiste)",
        "cluster_id": "numéro de cluster (K-Means, GMM ou DBSCAN ; -1 = bruit DBSCAN)",
        "score_churn": "risque de départ 0-1 (récence / fenêtre d'analyse)",
        "date_maj_segmentation": "date du dernier calcul de segmentation",
    },
    "transactions": {
        "transaction_id": "uuid, clé primaire",
        "client_id": "uuid -> clients.client_id (peut être NULL)",
        "numero_facture": "numéro de facture (plusieurs lignes par facture)",
        "code_produit": "code produit (-> stocks_produits.code_produit)",
        "description_produit": "libellé du produit",
        "quantite": "quantité vendue (entier)",
        "prix_unitaire": "prix unitaire",
        "date_transaction": "date et heure de la vente",
        "pays": "pays de la vente",
        "est_retour": "booléen : TRUE = retour/avoir. Le CA EXCLUT toujours les retours",
    },
    "stocks_produits": {
        "stock_id": "uuid, clé primaire",
        "code_produit": "code produit",
        "nom_produit": "nom du produit",
        "quantite_disponible": "unités en stock",
        "seuil_alerte": "stock <= ce seuil : alerte critique",
        "seuil_reapprovisionnement": "stock <= ce seuil : à réapprovisionner (alerte attention)",
        "delai_livraison_jours": "délai fournisseur en jours",
        "cout_unitaire": "coût d'achat unitaire",
        "date_maj": "date de mise à jour du stock",
    },
    "tresorerie": {
        "mouvement_id": "uuid, clé primaire",
        "date_mouvement": "date du mouvement",
        "type_mouvement": "'encaissement' ou 'decaissement'",
        "categorie": "catégorie du mouvement",
        "montant": "montant du mouvement (toujours positif ; le sens est donné par type_mouvement)",
        "solde_apres_mouvement": "solde après le mouvement (peut être NULL)",
        "commentaire": "commentaire libre",
    },
    "historique_segmentation": {
        "execution_id": "uuid, clé primaire",
        "date_execution": "date de l'exécution du pipeline de segmentation RFM",
        "algorithme": "'kmeans', 'gmm' ou 'dbscan'",
        "n_clients": "clients segmentés (actifs dans la fenêtre d'analyse)",
        "n_clients_hors_fenetre": "clients sans achat dans la fenêtre (classés Perdu)",
        "fenetre_jours": "fenêtre d'analyse RFM en jours",
        "date_reference": "dernière date de transaction utilisée comme référence de récence",
        "k_choisi": "nombre de clusters retenu (max de silhouette)",
        "eps_choisi": "epsilon retenu (DBSCAN uniquement)",
        "silhouette": "score de silhouette du clustering retenu (-1 à 1, plus haut = mieux séparé)",
        "davies_bouldin": "indice de Davies-Bouldin (plus bas = mieux)",
        "calinski_harabasz": "indice de Calinski-Harabasz (plus haut = mieux)",
        "repartition_segments": "texte JSON {segment: nombre de clients}",
        "diagnostics": "texte JSON : métriques pour chaque k testé (k, inertie, silhouette, davies_bouldin, ...)",
        "avertissements": "texte JSON : avertissements du pipeline",
    },
}

# Colonnes jamais exposées, quelle que soit la configuration.
COLONNES_TOUJOURS_INTERDITES = frozenset({"mot_de_passe_hache"})
DESCRIPTION_EMAIL = "e-mail du client (donnée personnelle)"

TABLES_AUTORISEES = frozenset(SCHEMA_EXPOSE)
REQUETES_MAX = 3  # requêtes SQL par question (pour croiser plusieurs sources)


def acces_emails() -> bool:
    """`COPILOTE_ACCES_EMAILS` (désactivé par défaut) : les e-mails des
    clients sont des données personnelles, et la question comme les lignes
    lues partent chez le fournisseur LLM externe configuré."""
    return bool(settings.COPILOTE_ACCES_EMAILS)


def colonnes_interdites() -> frozenset[str]:
    return COLONNES_TOUJOURS_INTERDITES | (frozenset() if acces_emails() else frozenset({"email"}))


def schema_texte() -> str:
    """Schéma montré au LLM (suit la configuration des e-mails)."""
    lignes = []
    for table, colonnes in SCHEMA_EXPOSE.items():
        colonnes = dict(colonnes)
        if table == "clients" and acces_emails():
            colonnes["email"] = DESCRIPTION_EMAIL
        lignes.append(f"- {table}(" + ", ".join(f"{c} : {d}" for c, d in colonnes.items()) + ")")
    return "\n".join(lignes)
_FONCTIONS_INTERDITES = frozenset(
    {
        "dblink", "dblink_exec", "lo_import", "lo_export", "lo_get", "lo_put",
        "set_config", "current_setting", "query_to_xml", "query_to_xmlschema",
        "nextval", "setval", "txid_current", "load_extension", "readfile", "writefile",
    }
)


def _types_exp(*noms: str) -> tuple[type, ...]:
    """Classes sqlglot par nom, en ignorant celles absentes de la version installée."""
    return tuple(t for t in (getattr(exp, n, None) for n in noms) if isinstance(t, type))


_NOEUDS_ECRITURE = _types_exp(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Command", "Into", "Copy", "Grant", "Revoke", "Set", "Use",
    "Pragma", "Transaction", "Commit", "Rollback", "Lock", "Analyze", "Comment",
)



class RequeteRefusee(ValueError):
    """La requête ne passe pas la validation (message lisible par le LLM)."""


@dataclass
class ResultatRequete:
    sql: str
    colonnes: list[str]
    lignes: list[list] = field(default_factory=list)
    tronque: bool = False


# ── 1. Extraction de la requête dans la réponse du LLM ──────────────────────

_BLOC_SQL = re.compile(r"```(?:sql|postgresql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_DEBUT_REQUETE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def extraire_requetes(reponse_llm: str) -> list[str]:
    """Requêtes écrites par le LLM (une par bloc ```sql```, au plus
    REQUETES_MAX) ; liste vide s'il a répondu AUCUNE ou rien d'exploitable.
    Un bloc qui ne commence pas par SELECT/WITH est ignoré (jamais exécuté)."""
    texte = (reponse_llm or "").strip()
    blocs = [b.strip() for b in _BLOC_SQL.findall(texte)] or [texte]
    requetes = [b.rstrip().rstrip(";").strip() for b in blocs if _DEBUT_REQUETE.match(b)]
    return requetes[:REQUETES_MAX]


def extraire_sql(reponse_llm: str) -> str | None:
    """Première requête de la réponse (compatibilité)."""
    requetes = extraire_requetes(reponse_llm)
    return requetes[0] if requetes else None


# ── 2. Validation ────────────────────────────────────────────────────────────


def valider_requete(sql: str) -> str:
    """Valide la requête et la renvoie normalisée (dialecte PostgreSQL).
    Lève `RequeteRefusee` avec un message que le LLM peut comprendre pour
    corriger sa requête."""
    try:
        instructions = [i for i in sqlglot.parse(sql, read="postgres") if i is not None]
    except sqlglot.errors.ParseError as exc:
        raise RequeteRefusee(f"requête SQL invalide : {str(exc).splitlines()[0]}") from exc
    if len(instructions) != 1:
        raise RequeteRefusee("une seule instruction SQL est autorisée")
    arbre = instructions[0]
    if not isinstance(arbre, exp.Query):
        raise RequeteRefusee("seules les requêtes de lecture (SELECT / WITH) sont autorisées")

    noms_cte = {cte.alias_or_name.lower() for cte in arbre.find_all(exp.CTE)}
    for noeud in arbre.walk():
        if isinstance(noeud, _NOEUDS_ECRITURE):
            raise RequeteRefusee(f"opération interdite ({type(noeud).__name__}) : lecture seule")
        if isinstance(noeud, exp.Table):
            nom = noeud.name.lower()
            if noeud.db and noeud.db.lower() != "public":
                raise RequeteRefusee(f"schéma interdit : {noeud.db}")
            if nom not in TABLES_AUTORISEES and nom not in noms_cte:
                raise RequeteRefusee(
                    f"table inconnue ou interdite : {nom} (tables disponibles : "
                    f"{', '.join(sorted(TABLES_AUTORISEES))})"
                )
        elif isinstance(noeud, exp.Column):
            if isinstance(noeud.this, exp.Star):
                raise RequeteRefusee("« table.* » interdit : lister les colonnes utiles")
            if noeud.name.lower() in colonnes_interdites():
                raise RequeteRefusee(f"colonne confidentielle interdite : {noeud.name}")
        elif isinstance(noeud, exp.Star) and not isinstance(noeud.parent, exp.Count):
            raise RequeteRefusee("SELECT * interdit : lister les colonnes utiles (COUNT(*) reste permis)")
        elif isinstance(noeud, exp.Func):
            nom = (noeud.name if isinstance(noeud, exp.Anonymous) else noeud.sql_name()).lower()
            if nom.startswith("pg_") or nom in _FONCTIONS_INTERDITES:
                raise RequeteRefusee(f"fonction système interdite : {nom}")
    return arbre.sql(dialect="postgres")


# ── 3. Exécution en lecture seule ────────────────────────────────────────────


@lru_cache(maxsize=1)
def _moteur_lecture(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=0)


def _moteur(db: Session) -> Engine:
    if settings.COPILOTE_DATABASE_URL_LECTURE:
        return _moteur_lecture(settings.COPILOTE_DATABASE_URL_LECTURE)
    return db.get_bind()


def _valeur_json(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if v is None or isinstance(v, int | float | str | bool):
        return v
    return str(v)  # uuid, etc.


def executer_requete(db: Session, sql_valide: str) -> ResultatRequete:
    """Exécute une requête DÉJÀ validée, dans une transaction en lecture
    seule, sur une connexion séparée de la session de l'API."""
    moteur = _moteur(db)
    dialecte = moteur.dialect.name
    sql_execute = sql_valide if dialecte == "postgresql" else sqlglot.transpile(
        sql_valide, read="postgres", write=dialecte
    )[0]
    with moteur.connect() as conn:
        if dialecte == "postgresql":
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {TIMEOUT_REQUETE_MS}")
        elif dialecte == "sqlite":
            conn.exec_driver_sql("PRAGMA query_only = ON")
        try:
            resultat = conn.execute(text(sql_execute))
            colonnes = list(resultat.keys())
            lignes = resultat.fetchmany(LIGNES_MAX + 1)
        finally:
            conn.rollback()
            if dialecte == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = OFF")
    return ResultatRequete(
        sql=sql_valide,
        colonnes=colonnes,
        lignes=[[_valeur_json(v) for v in ligne] for ligne in lignes[:LIGNES_MAX]],
        tronque=len(lignes) > LIGNES_MAX,
    )


def formater_pour_prompt(res: ResultatRequete) -> str:
    """Résultat en tableau texte compact (séparateur « | ») pour le LLM."""
    if not res.lignes:
        return "(aucune ligne)"
    visibles = res.lignes[:LIGNES_DANS_LE_PROMPT]
    texte = [" | ".join(res.colonnes)]
    for ligne in visibles:
        texte.append(" | ".join("" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in ligne))
    total = f"{len(res.lignes)}{'+' if res.tronque else ''}"
    if len(res.lignes) > len(visibles) or res.tronque:
        texte.append(f"... ({len(visibles)} lignes montrées sur {total})")
    return "\n".join(texte)
