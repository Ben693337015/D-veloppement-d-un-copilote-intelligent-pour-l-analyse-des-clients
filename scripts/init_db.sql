-- ============================================================================
-- Copilote IA PME — Schéma de base de données PostgreSQL
-- Groupe 2 (Maslaw Garga Ibrahim & Abdoulmadjid Ben Yahya)
-- Socle commun mutualisé — cf. Cadrage §4 "Contrat d'interface"
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ----------------------------------------------------------------------------
-- Table clients
-- Enrichie par le sous-projet Abdoulmadjid (segment_rfm, score_churn)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    client_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code_client_externe  VARCHAR(64) UNIQUE,          -- CustomerID d'origine (dataset)
    nom                  VARCHAR(150),
    email                VARCHAR(150),
    pays                 VARCHAR(100),
    date_creation        TIMESTAMP NOT NULL DEFAULT now(),

    -- Colonnes calculées par le module RFM (sous-projet Abdoulmadjid)
    -- Ne jamais renommer sans mettre à jour le module Maslaw et le dashboard
    recence_jours        INTEGER,
    frequence_achats      INTEGER,
    montant_total         NUMERIC(14, 2),
    score_r               SMALLINT,
    score_f               SMALLINT,
    score_m               SMALLINT,
    segment_rfm           VARCHAR(50),                -- ex: "Fidèle", "À risque", "Occasionnel"
    cluster_id             SMALLINT,                   -- id du cluster K-Means/DBSCAN/GMM
    score_churn            NUMERIC(5, 4),              -- probabilité de churn [0,1]
    date_maj_segmentation   TIMESTAMP,

    created_at            TIMESTAMP NOT NULL DEFAULT now(),
    updated_at             TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clients_segment_rfm ON clients (segment_rfm);
CREATE INDEX IF NOT EXISTS idx_clients_score_churn ON clients (score_churn);

-- ----------------------------------------------------------------------------
-- Table transactions
-- Partagée entre les deux sous-projets : Maslaw (CA / BFR) et Abdoulmadjid (R/F/M)
-- Toute modification de schéma doit être communiquée au binôme (cf. skill RFM §5)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id              UUID REFERENCES clients (client_id) ON DELETE SET NULL,
    numero_facture          VARCHAR(64),                -- InvoiceNo d'origine (dataset)
    code_produit             VARCHAR(64),
    description_produit       TEXT,
    quantite                  INTEGER NOT NULL,
    prix_unitaire              NUMERIC(12, 2) NOT NULL,
    montant_total_ligne         NUMERIC(14, 2) GENERATED ALWAYS AS (quantite * prix_unitaire) STORED,
    date_transaction             TIMESTAMP NOT NULL,
    pays                          VARCHAR(100),
    est_retour                    BOOLEAN NOT NULL DEFAULT FALSE,   -- facture commençant par "C"

    created_at                    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transactions_client_id ON transactions (client_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date_transaction);
CREATE INDEX IF NOT EXISTS idx_transactions_produit ON transactions (code_produit);

-- ----------------------------------------------------------------------------
-- Table stocks_produits
-- Consommée par le sous-projet Maslaw (seuils d'alerte, réapprovisionnement)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stocks_produits (
    stock_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code_produit             VARCHAR(64) NOT NULL,
    nom_produit               VARCHAR(200),
    quantite_disponible        INTEGER NOT NULL DEFAULT 0,
    seuil_alerte                INTEGER NOT NULL DEFAULT 10,
    seuil_reapprovisionnement    INTEGER NOT NULL DEFAULT 20,
    delai_livraison_jours         INTEGER DEFAULT 7,
    cout_unitaire                  NUMERIC(12, 2),
    date_maj                        TIMESTAMP NOT NULL DEFAULT now(),

    created_at                     TIMESTAMP NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stocks_code_produit ON stocks_produits (code_produit);

-- ----------------------------------------------------------------------------
-- Table tresorerie
-- Consommée par le sous-projet Maslaw (suivi BFR, alertes de liquidités)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tresorerie (
    mouvement_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date_mouvement            DATE NOT NULL,
    type_mouvement             VARCHAR(20) NOT NULL CHECK (type_mouvement IN ('encaissement', 'decaissement')),
    categorie                   VARCHAR(100),               -- ex: "vente", "fournisseur", "salaire", "impot"
    montant                      NUMERIC(14, 2) NOT NULL,
    solde_apres_mouvement          NUMERIC(14, 2),
    reference_transaction            UUID REFERENCES transactions (transaction_id) ON DELETE SET NULL,
    commentaire                       TEXT,

    created_at                       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tresorerie_date ON tresorerie (date_mouvement);
CREATE INDEX IF NOT EXISTS idx_tresorerie_type ON tresorerie (type_mouvement);

-- ----------------------------------------------------------------------------
-- Table users
-- Authentification (Phase 4) — un seul rôle implicite, pas de RBAC
-- granulaire (décision produit : outil interne, périmètre "simple")
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email                    VARCHAR(255) NOT NULL UNIQUE,
    nom                       VARCHAR(150),
    mot_de_passe_hache         VARCHAR(255) NOT NULL,
    actif                       BOOLEAN NOT NULL DEFAULT TRUE,
    date_creation                TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- ----------------------------------------------------------------------------
-- Historique des exécutions du pipeline RFM (ajout du 13/10/2026) : métriques
-- et diagnostic de chaque segmentation écrite en base, lus par le copilote.
-- (Aussi créée au démarrage de l'API si elle manque : bases existantes.)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS historique_segmentation (
    execution_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date_execution          TIMESTAMP NOT NULL DEFAULT now(),
    algorithme              VARCHAR(20) NOT NULL,
    n_clients               INTEGER NOT NULL,
    n_clients_hors_fenetre  INTEGER DEFAULT 0,
    fenetre_jours           INTEGER NOT NULL,
    date_reference          TIMESTAMP,
    k_choisi                INTEGER,
    eps_choisi              DOUBLE PRECISION,
    silhouette              DOUBLE PRECISION,
    davies_bouldin          DOUBLE PRECISION,
    calinski_harabasz       DOUBLE PRECISION,
    repartition_segments    TEXT,   -- JSON {segment: nb clients}
    diagnostics             TEXT,   -- JSON [{k, inertie, silhouette, ...}]
    avertissements          TEXT,   -- JSON [texte]
    comparaison             TEXT,   -- JSON : évaluation des 3 algorithmes (mode auto)
    synthese                TEXT    -- JSON [texte]
);
-- Bases créées avant l'ajout de ces deux colonnes (script rejouable) :
ALTER TABLE historique_segmentation ADD COLUMN IF NOT EXISTS comparaison TEXT;
ALTER TABLE historique_segmentation ADD COLUMN IF NOT EXISTS synthese TEXT;
CREATE INDEX IF NOT EXISTS idx_historique_segmentation_date ON historique_segmentation (date_execution);

-- ----------------------------------------------------------------------------
-- Vue de contrôle rapide utilisée par /api/v1/analytics/kpis
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_kpis_globaux AS
SELECT
    (SELECT COUNT(*) FROM clients)                                       AS nb_clients,
    (SELECT COUNT(*) FROM transactions WHERE NOT est_retour)             AS nb_transactions,
    (SELECT COALESCE(SUM(montant_total_ligne), 0) FROM transactions
        WHERE NOT est_retour)                                            AS chiffre_affaires_total,
    (SELECT COALESCE(SUM(quantite_disponible), 0) FROM stocks_produits)  AS stock_total_unites,
    (SELECT COALESCE(SUM(CASE WHEN type_mouvement = 'encaissement' THEN montant
                               ELSE -montant END), 0) FROM tresorerie)   AS solde_tresorerie_estime;

-- ----------------------------------------------------------------------------
-- Rôle en LECTURE SEULE pour l'accès du copilote à la base
-- (app/services/sql_agent.py). Optionnel : il est utilisé seulement si
-- COPILOTE_DATABASE_URL_LECTURE pointe dessus dans .env, par exemple :
--   COPILOTE_DATABASE_URL_LECTURE=postgresql+psycopg2://copilote_lecture:lecture_change_me@db:5432/copilote_pme
-- Il ne peut que lire, n'a AUCUN accès à la table users, et ne voit pas la
-- colonne clients.email (droits accordés colonne par colonne).
-- Base déjà créée (volume existant) : exécuter ce bloc à la main avec
--   docker compose exec -T db psql -U copilote_user -d copilote_pme < scripts/init_db.sql
-- (les CREATE ... IF NOT EXISTS du début rendent le script rejouable).
-- ----------------------------------------------------------------------------
-- Si le propriétaire de la base n'a pas le droit CREATEROLE (hors Docker),
-- le bloc est ignoré avec un avertissement au lieu de faire échouer le script.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilote_lecture') THEN
        CREATE ROLE copilote_lecture LOGIN PASSWORD 'lecture_change_me';
    END IF;
    ALTER ROLE copilote_lecture SET default_transaction_read_only = on;
    ALTER ROLE copilote_lecture SET statement_timeout = '5s';
    GRANT USAGE ON SCHEMA public TO copilote_lecture;
    REVOKE ALL ON users FROM copilote_lecture;
    GRANT SELECT ON transactions, stocks_produits, tresorerie, historique_segmentation TO copilote_lecture;
    GRANT SELECT (client_id, code_client_externe, nom, pays, date_creation, recence_jours,
                  frequence_achats, montant_total, score_r, score_f, score_m, segment_rfm,
                  cluster_id, score_churn, date_maj_segmentation, created_at, updated_at)
        ON clients TO copilote_lecture;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE WARNING 'Rôle copilote_lecture non créé (droit CREATEROLE manquant) : le copilote '
                  'utilisera DATABASE_URL, protégé par la transaction en lecture seule.';
END
$$;
