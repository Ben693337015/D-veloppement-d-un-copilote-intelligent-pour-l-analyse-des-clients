import { useState } from "react";
import { genererRapportPdf } from "../api/client";
import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Alert } from "../components/ui/Alert";
import { IconDoc, IconDownload } from "../components/icons";

const SECTIONS_DISPONIBLES = {
  page_garde: "Page de garde",
  kpis_globaux: "KPIs globaux",
  coude_silhouette: "Coude & silhouette K-Means",
  distribution_segments: "Distribution des segments RFM",
  segments_summary: "Répartition & recommandations par segment",
  top_clients: "Extrait des meilleurs clients",
};

export function Rapport() {
  const [entreprise, setEntreprise] = useState("Mon Entreprise");
  const [auteur, setAuteur] = useState("Équipe");
  const [themeSombre, setThemeSombre] = useState(false);
  const [topN, setTopN] = useState(20);
  const [sections, setSections] = useState(Object.keys(SECTIONS_DISPONIBLES));
  const [chargement, setChargement] = useState(false);
  const [erreur, setErreur] = useState(null);
  const [pdfUrl, setPdfUrl] = useState(null);

  function basculerSection(cle) {
    setSections((s) => (s.includes(cle) ? s.filter((x) => x !== cle) : [...s, cle]));
  }

  async function genererPdf() {
    if (sections.length === 0) {
      setErreur("Sélectionnez au moins une section.");
      return;
    }
    setChargement(true);
    setErreur(null);
    setPdfUrl(null);
    try {
      const blob = await genererRapportPdf({
        entreprise,
        auteur,
        themeSombre,
        sections,
        topNClients: Number(topN),
      });
      setPdfUrl(URL.createObjectURL(blob));
    } catch (err) {
      setErreur(err.message);
    } finally {
      setChargement(false);
    }
  }

  const nomFichier = `rapport_copilote_pme_${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}.pdf`;

  return (
    <div>
      <PageHeader
        icon={IconDoc}
        eyebrow="Livrable final"
        title="Génération du rapport PDF"
        subtitle="Page de garde, KPIs, diagnostic K-Means, segments, top clients — assemblés à partir des vrais résultats des deux modules."
      />

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink">Entreprise</span>
            <input
              value={entreprise}
              onChange={(e) => setEntreprise(e.target.value)}
              className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink">Auteur</span>
            <input
              value={auteur}
              onChange={(e) => setAuteur(e.target.value)}
              className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={themeSombre} onChange={(e) => setThemeSombre(e.target.checked)} />
            🌙 Thème sombre
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            Clients dans l'extrait
            <input
              type="number"
              min={5}
              max={50}
              value={topN}
              onChange={(e) => setTopN(e.target.value)}
              className="w-20 rounded-xl border border-line px-3 py-1.5 text-sm outline-none focus:border-brand"
            />
          </label>
        </div>

        <p className="mb-2 mt-6 font-display text-[15px] font-bold text-ink">Sections à inclure</p>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(SECTIONS_DISPONIBLES).map(([cle, label]) => (
            <label key={cle} className="flex items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={sections.includes(cle)} onChange={() => basculerSection(cle)} />
              {label}
            </label>
          ))}
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button onClick={genererPdf} loading={chargement}>
            📄 Générer le PDF
          </Button>
          {pdfUrl && (
            <a
              href={pdfUrl}
              download={nomFichier}
              className="inline-flex items-center gap-2 rounded-xl bg-success-bg px-4 py-2.5 text-sm font-semibold text-success transition-colors hover:opacity-85"
            >
              <IconDownload width={16} height={16} />
              Télécharger le rapport
            </a>
          )}
        </div>

        {erreur && <Alert tone="danger" className="mt-4">{erreur}</Alert>}
        {pdfUrl && <Alert tone="success" className="mt-4">Rapport généré.</Alert>}
      </Card>
    </div>
  );
}
