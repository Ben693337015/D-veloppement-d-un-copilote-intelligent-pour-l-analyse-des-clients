"""Import générique -> tables `clients` + `transactions`.

Remplace la logique auparavant codée en dur dans load_online_retail.py :
cette version accepte N'IMPORTE QUEL nom de colonne source, via un mapping
fourni par l'utilisateur (cf. app/schemas/ingestion.py::TransactionColumnMapping),
au lieu de supposer `CustomerID`, `InvoiceDate`, `UnitPrice`, etc.

Décisions de nettoyage (documentées pour la soutenance / le rapport,
cf. Cadrage Tâche #10) :

  1. Cast des types (date -> datetime, montant -> float).
  2. Lignes sans date ou sans identifiant client -> rejetées (le RFM et les
     séries temporelles ont besoin de ces deux axes).
  3. Normalisation de l'ID client (strip + upper) pour éviter les doublons
     "123" vs " 123 " vs "abc" vs "ABC" (bug fréquent constaté sur les
     exports PME).
  4. Détection des retours (est_retour) : quantité négative OU montant
     négatif OU numéro de facture de type annulation/avoir ("C536379",
     "A563185" pour Online Retail, "AV-…"/"AVOIR …" pour les ERP français —
     jamais un simple préfixe C/A, cf. commentaire dans le code). IMPORTANT : contrairement à `prepare_df` de
     BEST_RFM_v2 qui *supprime* les montants <= 0, on les CONSERVE en les
     marquant `est_retour=True` — car analytics_service et marketing_service
     filtrent déjà `~Transaction.est_retour` pour le CA. Les supprimer à
     l'ingestion ferait perdre une information métier réelle (taux de
     retour), utile pour Sophie (persona marketing) et Marc (persona stock).
  5. Déduplication des lignes strictement identiques (client + date +
     montant + produit).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import Client, Transaction
from app.schemas.ingestion import ImportResult, TransactionColumnMapping


def _resolve_montant(df: pd.DataFrame, mapping: TransactionColumnMapping) -> tuple[pd.DataFrame, str | None]:
    """Calcule quantite/prix_unitaire de façon homogène quel que soit le mode
    de mapping choisi (montant total direct, quantité x prix unitaire, ou
    montant total + quantité réelle sans prix unitaire explicite).

    BUG CORRIGÉ : un fichier PME fournissant à la fois `montant_col`
    (montant total déjà calculé) ET `quantite_col`, mais SANS
    `prix_unitaire_col`, passait auparavant dans la branche "montant_col
    seul" — la vraie quantité vendue (ex. 4, 9, 12 unités) était alors
    silencieusement écrasée à 1 pour CHAQUE ligne, sans aucun avertissement
    dans `ImportResult.avertissements`. Sans impact sur le CA (toujours
    recalculé en quantite * prix_unitaire, donc mathématiquement inchangé),
    mais avec un impact réel et invisible sur la colonne `quantite` stockée
    en base — utilisée telle quelle par tout futur module de volumétrie
    (prévision de la demande en unités, stock). On reconstitue maintenant
    le prix unitaire implicite (montant / quantité) pour préserver la
    vraie quantité, avec un repli explicite et SIGNALÉ (pas silencieux)
    à 1 unité uniquement pour les lignes où la quantité fournie est
    nulle, manquante ou invalide.
    """
    df = df.copy()
    avertissement: str | None = None
    if mapping.quantite_col and mapping.prix_unitaire_col:
        df["quantite_val"] = pd.to_numeric(df[mapping.quantite_col], errors="coerce")
        df["prix_unitaire_val"] = pd.to_numeric(df[mapping.prix_unitaire_col], errors="coerce")
    elif mapping.montant_col and mapping.quantite_col:
        montant = pd.to_numeric(df[mapping.montant_col], errors="coerce")
        quantite = pd.to_numeric(df[mapping.quantite_col], errors="coerce")
        quantite_exploitable = quantite.where(quantite.abs() > 1e-9)  # évite la division par zéro
        df["quantite_val"] = quantite
        df["prix_unitaire_val"] = (montant / quantite_exploitable).abs()

        repli = quantite_exploitable.isna() & montant.notna()
        if repli.any():
            df.loc[repli, "quantite_val"] = montant.loc[repli].apply(
                lambda v: -1 if pd.notna(v) and v < 0 else 1
            )
            df.loc[repli, "prix_unitaire_val"] = montant.loc[repli].abs()
            avertissement = (
                f"{int(repli.sum())} ligne(s) avec quantité nulle, manquante ou invalide dans "
                f"'{mapping.quantite_col}' : quantité repliée à 1 pour ces lignes uniquement "
                "(montant total conservé)."
            )
    else:
        # montant_col seul : on modélise comme 1 unité au prix = montant,
        # ce qui reste cohérent avec montant_total_ligne = quantite * prix_unitaire
        # côté PostgreSQL (colonne générée, cf. scripts/init_db.sql).
        montant = pd.to_numeric(df[mapping.montant_col], errors="coerce")
        df["quantite_val"] = montant.apply(lambda v: -1 if pd.notna(v) and v < 0 else 1)
        df["prix_unitaire_val"] = montant.abs()
    return df, avertissement


def import_transactions_generic(
    db: Session,
    df: pd.DataFrame,
    mapping: TransactionColumnMapping,
    batch_size: int = 5000,
) -> ImportResult:
    n_lignes_lues = len(df)
    avertissements: list[str] = []

    for col in (mapping.date_col, mapping.client_col):
        if col not in df.columns:
            raise ValueError(f"Colonne mappée introuvable dans le fichier : '{col}'")

    df, avertissement_quantite = _resolve_montant(df, mapping)
    if avertissement_quantite:
        avertissements.append(avertissement_quantite)
    df["date_val"] = pd.to_datetime(df[mapping.date_col], errors="coerce")
    df["client_id_ext_val"] = df[mapping.client_col].astype(str).str.strip().str.upper()
    df.loc[df[mapping.client_col].isna(), "client_id_ext_val"] = None

    # ── Rejet des lignes sans date / client / montant exploitables ──────────
    mask_valide = df["date_val"].notna() & df["client_id_ext_val"].notna() & df["prix_unitaire_val"].notna()
    n_rejetees = int((~mask_valide).sum())
    df = df[mask_valide].copy()

    if n_rejetees:
        avertissements.append(
            f"{n_rejetees} ligne(s) rejetée(s) : date, identifiant client ou montant manquant/invalide."
        )

    # ── Détection des retours (conservés, jamais supprimés) ─────────────────
    # BUG CORRIGÉ : l'ancienne règle `.str.startswith(("C", "A"))` marquait
    # comme retour TOUTE facture dont le numéro commence par C ou A — donc
    # 100 % des ventes d'une PME numérotant ses pièces "CMD-2024-001",
    # "CDE-…", "ART-…" ou "ACH-…" (CA affiché = 0, segmentation vide).
    # On ne reconnaît plus que les conventions réelles d'annulation/avoir :
    #   - lettre C ou A IMMÉDIATEMENT suivie d'un chiffre (Online Retail II :
    #     "C536379" = annulation, "A563185" = ajustement) ;
    #   - préfixe d'avoir français "AV", "AVO", "AVOIR" suivi d'un numéro
    #     ("AV-2024-001", "AVOIR 12").
    # Les retours sans numéro reconnaissable restent détectés par le signe
    # de la quantité ou du montant (règle ci-dessous, inchangée).
    facture_col = mapping.numero_facture_col
    if facture_col and facture_col in df.columns:
        prefixe_retour = (
            df[facture_col].astype(str).str.strip().str.upper()
            .str.match(r"^(?:[CA]\d|AV(?:OIR|O)?[\s\-_/]*\d)")
        )
    else:
        prefixe_retour = pd.Series(False, index=df.index)
    df["est_retour_val"] = (df["quantite_val"] < 0) | (df["prix_unitaire_val"] < 0) | prefixe_retour
    df["prix_unitaire_val"] = df["prix_unitaire_val"].abs()
    df["quantite_val"] = df["quantite_val"].abs()

    # ── Déduplication exacte ──────────────────────────────────────────────
    dedup_cols = ["client_id_ext_val", "date_val", "prix_unitaire_val", "quantite_val"]
    if mapping.code_produit_col and mapping.code_produit_col in df.columns:
        dedup_cols.append(mapping.code_produit_col)
    # BUG CORRIGÉ : sans le numéro de facture dans la clé, deux COMMANDES
    # DISTINCTES d'un même client, le même jour, pour le même article au même
    # prix (ex. AdventureWorks SO73199 et SO73201) étaient fusionnées et l'une
    # supprimée comme « doublon » — perte de CA et de fréquence RFM. Une vraie
    # ligne dupliquée par l'export garde, elle, le même numéro : toujours retirée.
    if facture_col and facture_col in df.columns:
        dedup_cols.append(facture_col)
    n_avant_dedup = len(df)
    df = df.drop_duplicates(subset=dedup_cols)
    n_doublons = n_avant_dedup - len(df)
    if n_doublons:
        avertissements.append(f"{n_doublons} doublon(s) exact(s) supprimé(s).")

    # ── Normalisation des noms de colonnes avant itertuples() ───────────────
    # BUG ÉVITÉ : itertuples() transforme silencieusement en positionnel
    # (Pandas(_1=..., _2=...)) toute colonne dont le nom n'est pas un
    # identifiant Python valide (espaces, accents, tirets...) — très courant
    # dans un export PME ("Numéro Facture", "Code Produit"). On renomme donc
    # explicitement les colonnes mappées vers des noms internes sûrs et on
    # n'utilise plus jamais mapping.xxx_col comme nom d'attribut de tuple.
    rename_map: dict[str, str] = {}
    if facture_col and facture_col in df.columns:
        rename_map[facture_col] = "facture_val"
    if mapping.code_produit_col and mapping.code_produit_col in df.columns:
        rename_map[mapping.code_produit_col] = "code_produit_val"
    if mapping.description_produit_col and mapping.description_produit_col in df.columns:
        rename_map[mapping.description_produit_col] = "description_produit_val"
    if mapping.pays_col and mapping.pays_col in df.columns:
        rename_map[mapping.pays_col] = "pays_val"
    if mapping.nom_client_col and mapping.nom_client_col in df.columns:
        rename_map[mapping.nom_client_col] = "nom_client_val"
    if mapping.email_client_col and mapping.email_client_col in df.columns:
        rename_map[mapping.email_client_col] = "email_client_val"
    df = df.rename(columns=rename_map)

    cols_utiles = ["client_id_ext_val", "date_val", "quantite_val", "prix_unitaire_val", "est_retour_val"]
    cols_utiles += [c for c in rename_map.values() if c not in cols_utiles]
    df_iter = df[cols_utiles]

    # ── Import : clients puis transactions, par opérations groupées ────────
    # OPTIMISATION (test de bout en bout, 60 398 lignes / 18 484 clients sur
    # PostgreSQL : 70 s -> quelques secondes). L'ancienne boucle faisait, pour
    # CHAQUE nouveau client, un SELECT puis un INSERT + flush (≈ 37 000
    # allers-retours), puis rechargeait chaque client après chaque commit
    # intermédiaire (objets expirés par SQLAlchemy). Désormais :
    #   1. un seul SELECT (par paquets) des clients déjà connus ;
    #   2. création de tous les nouveaux clients en un seul flush ;
    #   3. enrichissement nom/e-mail/pays une fois par client ;
    #   4. insertion des transactions par lots (INSERT multi-lignes).
    codes = list(dict.fromkeys(df_iter["client_id_ext_val"]))
    clients: dict[str, Client] = {}
    for i in range(0, len(codes), 1000):
        paquet = codes[i : i + 1000]
        for c in db.query(Client).filter(Client.code_client_externe.in_(paquet)):
            clients[c.code_client_externe] = c
    nouveaux = [Client(code_client_externe=code) for code in codes if code not in clients]
    db.add_all(nouveaux)
    db.flush()
    for c in nouveaux:
        clients[c.code_client_externe] = c
    codes_crees = {c.code_client_externe for c in nouveaux}
    n_enregistrements_crees = len(nouveaux)

    # Enrichissement NON DESTRUCTIF : nom/e-mail/pays renseignés seulement
    # s'ils sont vides (jamais d'écrasement d'une saisie manuelle de la PME),
    # avec la première valeur non vide rencontrée dans le fichier.
    n_enregistrements_maj = 0
    champs_enrichis = [(col, attr) for col, attr in
                       (("nom_client_val", "nom"), ("email_client_val", "email"), ("pays_val", "pays"))
                       if col in df_iter.columns]
    if champs_enrichis:
        premieres = df_iter.groupby("client_id_ext_val", sort=False)[[c for c, _ in champs_enrichis]].first()
        for code, valeurs in premieres.iterrows():
            client = clients[code]
            maj = False
            for col, attr in champs_enrichis:
                v = valeurs[col]
                if v is not None and pd.notna(v) and getattr(client, attr) is None:
                    setattr(client, attr, str(v))
                    maj = True
            # Un client CRÉÉ par cet import n'est pas compté comme « mis à jour ».
            if maj and code not in codes_crees:
                n_enregistrements_maj += 1
    ids_clients = {code: c.client_id for code, c in clients.items()}
    db.commit()

    def _texte(v):
        return str(v) if v is not None and pd.notna(v) else None

    a_colonne = set(df_iter.columns)
    lot: list[dict] = []
    for row in df_iter.itertuples(index=False):
        lot.append({
            "transaction_id": uuid.uuid4(),
            "client_id": ids_clients[row.client_id_ext_val],
            "numero_facture": _texte(row.facture_val) if "facture_val" in a_colonne else None,
            "code_produit": _texte(row.code_produit_val) if "code_produit_val" in a_colonne else None,
            "description_produit": (
                _texte(row.description_produit_val) if "description_produit_val" in a_colonne else None
            ),
            "quantite": int(row.quantite_val),
            "prix_unitaire": float(row.prix_unitaire_val),
            "date_transaction": row.date_val.to_pydatetime(),
            "pays": _texte(row.pays_val) if "pays_val" in a_colonne else None,
            "est_retour": bool(row.est_retour_val),
            "created_at": datetime.utcnow(),
        })
        if len(lot) >= batch_size:
            db.execute(insert(Transaction), lot)
            db.commit()
            lot = []
    if lot:
        db.execute(insert(Transaction), lot)
    db.commit()

    return ImportResult(
        cible="transactions",
        n_lignes_lues=n_lignes_lues,
        n_lignes_importees=len(df),
        n_lignes_rejetees=n_rejetees,
        n_doublons_supprimes=n_doublons,
        n_enregistrements_crees=n_enregistrements_crees,
        n_enregistrements_maj=n_enregistrements_maj,
        avertissements=avertissements,
    )
