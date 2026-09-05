import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { importDataset, previewDataset } from "../api/client";
import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Alert } from "../components/ui/Alert";
import { Select } from "../components/ui/Select";
import { FileDropzone } from "../components/ui/FileDropzone";
import { Table } from "../components/ui/Table";
import { Spinner } from "../components/ui/Spinner";
import { IconUpload } from "../components/icons";

const CHAMPS_PAR_CIBLE = {
  transactions: [
    { cle: "date_col", label: "Colonne Date", obligatoire: true },
    { cle: "client_col", label: "Colonne Client (identifiant)", obligatoire: true },
    { cle: "montant_col", label: "Colonne Montant (total ligne)", obligatoire: false },
    { cle: "quantite_col", label: "Colonne Quantité", obligatoire: false },
    { cle: "prix_unitaire_col", label: "Colonne Prix unitaire", obligatoire: false },
    { cle: "code_produit_col", label: "Colonne Code produit", obligatoire: false },
    { cle: "description_produit_col", label: "Colonne Désignation produit", obligatoire: false },
    { cle: "numero_facture_col", label: "Colonne N° facture", obligatoire: false },
    { cle: "pays_col", label: "Colonne Pays", obligatoire: false },
    { cle: "nom_client_col", label: "Colonne Nom client", obligatoire: false },
    { cle: "email_client_col", label: "Colonne Email client", obligatoire: false },
  ],
  stocks: [
    { cle: "code_produit_col", label: "Colonne Code produit", obligatoire: true },
    { cle: "quantite_disponible_col", label: "Colonne Quantité disponible", obligatoire: true },
    { cle: "nom_produit_col", label: "Colonne Nom produit", obligatoire: false },
    { cle: "seuil_alerte_col", label: "Colonne Seuil d'alerte", obligatoire: false },
    { cle: "seuil_reappro_col", label: "Colonne Seuil de réapprovisionnement", obligatoire: false },
    { cle: "cout_unitaire_col", label: "Colonne Coût unitaire", obligatoire: false },
    { cle: "delai_livraison_col", label: "Colonne Délai de livraison (jours)", obligatoire: false },
  ],
  tresorerie: [
    { cle: "date_col", label: "Colonne Date", obligatoire: true },
    { cle: "montant_col", label: "Colonne Montant", obligatoire: true },
    { cle: "type_mouvement_col", label: "Colonne Type de mouvement", obligatoire: false },
    { cle: "categorie_col", label: "Colonne Catégorie", obligatoire: false },
  ],
};

const ONGLETS = [
  { cible: "transactions", label: "🧾 Ventes (transactions)", aide: "Alimente à la fois le module RFM et le module ventes/BFR — table partagée. Fournissez soit Montant, soit Quantité + Prix unitaire." },
  { cible: "stocks", label: "📦 Stock", aide: "Alimente les alertes de réapprovisionnement." },
  { cible: "tresorerie", label: "💰 Trésorerie", aide: "Alimente le solde de trésorerie estimé." },
];

function OngletImport({ cible, aide }) {
  const [fichier, setFichier] = useState(null);
  const [apercu, setApercu] = useState(null);
  const [mapping, setMapping] = useState({});
  const [chargementApercu, setChargementApercu] = useState(false);
  const [chargementImport, setChargementImport] = useState(false);
  const [erreur, setErreur] = useState(null);
  const [resultat, setResultat] = useState(null);

  const champs = CHAMPS_PAR_CIBLE[cible];

  async function gererFichier(fichierChoisi) {
    setFichier(fichierChoisi);
    setResultat(null);
    setErreur(null);
    setChargementApercu(true);
    try {
      const data = await previewDataset(fichierChoisi);
      setApercu(data);
      const suggestion = data.mapping_suggere[cible] || {};
      const initial = {};
      for (const { cle } of champs) {
        initial[cle] = suggestion[cle] && data.nom_fichier_colonnes.includes(suggestion[cle]) ? suggestion[cle] : "";
      }
      setMapping(initial);
    } catch (err) {
      setErreur(err.message);
      setApercu(null);
    } finally {
      setChargementApercu(false);
    }
  }

  const champsObligatoiresManquants = useMemo(
    () => champs.filter((c) => c.obligatoire && !mapping[c.cle]).map((c) => c.label),
    [champs, mapping],
  );

  const montantIncomplet =
    cible === "transactions" &&
    !mapping.montant_col &&
    !(mapping.quantite_col && mapping.prix_unitaire_col);

  async function lancerImport() {
    setChargementImport(true);
    setErreur(null);
    try {
      const mappingPropre = Object.fromEntries(
        Object.entries(mapping).filter(([, v]) => v),
      );
      const data = await importDataset(cible, fichier, mappingPropre);
      setResultat(data);
    } catch (err) {
      setErreur(err.message);
    } finally {
      setChargementImport(false);
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm leading-relaxed text-muted">{aide}</p>

      <FileDropzone fichierActuel={fichier} onFileSelected={gererFichier} />

      {chargementApercu && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner size={16} className="text-brand" />
          Analyse du fichier — détection des colonnes…
        </div>
      )}

      {erreur && <Alert tone="danger">{erreur}</Alert>}

      {apercu && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-xl bg-paper px-4 py-3">
              <p className="text-xs text-muted">Lignes détectées</p>
              <p className="font-tabular text-xl font-semibold text-ink">{apercu.n_lignes}</p>
            </div>
            <div className="rounded-xl bg-paper px-4 py-3">
              <p className="text-xs text-muted">Colonnes détectées</p>
              <p className="font-tabular text-xl font-semibold text-ink">{apercu.n_colonnes}</p>
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Aperçu des 10 premières lignes
            </p>
            <Table
              columns={apercu.nom_fichier_colonnes.map((c) => ({ key: c, header: c }))}
              rows={apercu.apercu_lignes.map((r, i) => ({ id: i, ...r }))}
            />
          </div>

          <div>
            <p className="mb-1 font-display text-[15px] font-bold text-ink">⚙️ Configuration des colonnes</p>
            <p className="mb-4 text-sm text-muted">
              L'application a détecté automatiquement les colonnes ci-dessous. Vérifiez et corrigez
              les associations avant de lancer l'import. (* = obligatoire)
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {champs.map(({ cle, label, obligatoire }) => (
                <Select
                  key={cle}
                  label={label}
                  required={obligatoire}
                  value={mapping[cle] || ""}
                  onChange={(e) => setMapping((m) => ({ ...m, [cle]: e.target.value }))}
                >
                  <option value="">— Aucune —</option>
                  {apercu.nom_fichier_colonnes.map((col) => (
                    <option key={col} value={col}>
                      {col}
                    </option>
                  ))}
                </Select>
              ))}
            </div>
            {montantIncomplet && (
              <Alert tone="warning" className="mt-3">
                Renseignez soit <strong>Colonne Montant</strong>, soit <strong>Quantité</strong> +{" "}
                <strong>Prix unitaire</strong>.
              </Alert>
            )}
          </div>

          <Button
            onClick={lancerImport}
            loading={chargementImport}
            disabled={champsObligatoiresManquants.length > 0 || montantIncomplet}
          >
            ✅ Lancer l'import
          </Button>
          {champsObligatoiresManquants.length > 0 && (
            <p className="text-xs text-danger">
              Champs obligatoires manquants : {champsObligatoiresManquants.join(", ")}
            </p>
          )}
        </>
      )}

      {resultat && (
        <Card className="bg-paper/60">
          <p className="mb-3 text-sm font-semibold text-success">✅ Import terminé.</p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ["Lignes lues", resultat.n_lignes_lues],
              ["Lignes importées", resultat.n_lignes_importees],
              ["Lignes rejetées", resultat.n_lignes_rejetees],
              ["Doublons supprimés", resultat.n_doublons_supprimes],
            ].map(([label, val]) => (
              <div key={label}>
                <p className="text-xs text-muted">{label}</p>
                <p className="font-tabular text-lg font-semibold text-ink">{val}</p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted">
            Enregistrements créés : {resultat.n_enregistrements_crees} · mis à jour :{" "}
            {resultat.n_enregistrements_maj}
          </p>
          {resultat.avertissements.map((a, i) => (
            <Alert key={i} tone="warning" className="mt-2">
              {a}
            </Alert>
          ))}
          {cible === "transactions" && (
            <Alert tone="info" className="mt-3">
              ➡️ Données prêtes. Rendez-vous sur{" "}
              <Link to="/segmentation" className="font-semibold underline underline-offset-2">
                Segmentation RFM
              </Link>{" "}
              et{" "}
              <Link to="/ventes" className="font-semibold underline underline-offset-2">
                Ventes &amp; Stocks
              </Link>{" "}
              pour lancer les modèles des deux modules sur ce jeu de données.
            </Alert>
          )}
        </Card>
      )}
    </div>
  );
}

export function Donnees() {
  const [ongletActif, setOngletActif] = useState("transactions");

  return (
    <div>
      <PageHeader
        icon={IconUpload}
        eyebrow="Étape 1"
        title="Chargement des données"
        subtitle="Fonctionne avec le fichier d'une PME quelconque — pas seulement Online Retail II. Le prétraitement (nettoyage, détection des retours, déduplication, plafonnement des valeurs extrêmes) est appliqué automatiquement à l'import."
      />

      <Card padded={false}>
        <div className="flex border-b border-line">
          {ONGLETS.map((o) => (
            <button
              key={o.cible}
              onClick={() => setOngletActif(o.cible)}
              className={`flex-1 border-b-2 px-4 py-3.5 text-sm font-semibold transition-colors ${
                ongletActif === o.cible
                  ? "border-brand text-brand"
                  : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="p-5 sm:p-6">
          {ONGLETS.map(
            (o) =>
              ongletActif === o.cible && <OngletImport key={o.cible} cible={o.cible} aide={o.aide} />,
          )}
        </div>
      </Card>
    </div>
  );
}
