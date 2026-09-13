import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getClients, getSegmentsSummary, runRfmPipeline } from "../api/client";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardTitle } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Alert } from "../components/ui/Alert";
import { EmptyState } from "../components/ui/EmptyState";
import { Table } from "../components/ui/Table";
import { SegmentBadge } from "../components/ui/Badge";
import { Select } from "../components/ui/Select";
import { IconTarget } from "../components/icons";

const COULEUR_PRIMAIRE = "#2f5d8a";
const COULEUR_ACCENT = "#d98e3d";

export function Segmentation() {
  const [algorithme, setAlgorithme] = useState("kmeans");
  const [kMin, setKMin] = useState(2);
  const [kMax, setKMax] = useState(6);
  const [fenetreJours, setFenetreJours] = useState(365);
  const [winsorPercentile, setWinsorPercentile] = useState(99);
  const [dryRun, setDryRun] = useState(false);
  const [parametresOuverts, setParametresOuverts] = useState(false);

  const [chargement, setChargement] = useState(false);
  const [erreur, setErreur] = useState(null);
  const [resultat, setResultat] = useState(null);
  const [segments, setSegments] = useState([]);
  const [clients, setClients] = useState([]);
  const [filtreSegment, setFiltreSegment] = useState("Tous");

  async function chargerSegmentsEtClients(segmentFiltre) {
    const [segs, cls] = await Promise.all([
      getSegmentsSummary(),
      getClients({ segment: segmentFiltre === "Tous" ? undefined : segmentFiltre, limit: 200 }),
    ]);
    setSegments(segs);
    setClients(cls);
  }

  async function lancerPipeline() {
    setChargement(true);
    setErreur(null);
    try {
      const data = await runRfmPipeline({
        algorithme,
        kMin: Number(kMin),
        kMax: Number(kMax),
        fenetreJours: Number(fenetreJours),
        winsorPercentile: Number(winsorPercentile),
        dryRun,
      });
      setResultat(data);
      if (data.n_clients > 0) {
        await chargerSegmentsEtClients(filtreSegment);
      }
    } catch (err) {
      setErreur(err.message);
    } finally {
      setChargement(false);
    }
  }

  async function changerFiltre(segment) {
    setFiltreSegment(segment);
    try {
      const cls = await getClients({ segment: segment === "Tous" ? undefined : segment, limit: 200 });
      setClients(cls);
    } catch (err) {
      setErreur(err.message);
    }
  }

  return (
    <div>
      <PageHeader
        icon={IconTarget}
        eyebrow="Module Abdoulmadjid"
        title="Segmentation RFM & clustering"
        subtitle="Calcul RFM, diagnostic K-Means (coude et silhouette), segmentation automatique des clients."
        actions={
          <Button variant="secondary" onClick={() => setParametresOuverts((v) => !v)}>
            ⚙️ Paramètres
          </Button>
        }
      />

      {parametresOuverts && (
        <Card className="mb-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Select
                label="Algorithme"
                value={algorithme}
                onChange={(e) => setAlgorithme(e.target.value)}
              >
                <option value="kmeans">K-Means</option>
                <option value="dbscan">DBSCAN (isole les atypiques)</option>
                <option value="gmm">Gaussian Mixture (GMM)</option>
              </Select>
            </div>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">
                k minimum {algorithme === "dbscan" && <span className="text-muted">(non utilisé)</span>}
              </span>
              <input
                type="number"
                min={2}
                max={9}
                value={kMin}
                disabled={algorithme === "dbscan"}
                onChange={(e) => setKMin(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand disabled:cursor-not-allowed disabled:opacity-40"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">
                k maximum {algorithme === "dbscan" && <span className="text-muted">(non utilisé)</span>}
              </span>
              <input
                type="number"
                min={Number(kMin)}
                max={10}
                value={kMax}
                disabled={algorithme === "dbscan"}
                onChange={(e) => setKMax(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand disabled:cursor-not-allowed disabled:opacity-40"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">Fenêtre d'analyse (jours)</span>
              <input
                type="number"
                min={30}
                max={1095}
                value={fenetreJours}
                onChange={(e) => setFenetreJours(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">Percentile de plafonnement</span>
              <input
                type="number"
                min={90}
                max={99.9}
                step={0.1}
                value={winsorPercentile}
                onChange={(e) => setWinsorPercentile(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
              />
            </label>
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            Mode exploration (dry-run) — ne pas écrire les segments en base
          </label>
        </Card>
      )}

      <Button onClick={lancerPipeline} loading={chargement} className="mb-6">
        ▶️ Lancer le pipeline RFM
      </Button>

      {erreur && <Alert tone="danger" className="mb-6">{erreur}</Alert>}

      {!resultat && !chargement && (
        <EmptyState
          icon={IconTarget}
          title="Aucun résultat pour le moment"
          description="Lancez le pipeline pour afficher le diagnostic et la segmentation."
        />
      )}

      {resultat && resultat.n_clients === 0 && (
        <Alert tone="warning">
          Aucun client exploitable sur cette fenêtre d'analyse. {resultat.avertissements.join(" ")}
        </Alert>
      )}

      {resultat && resultat.n_clients > 0 && (
        <div className="space-y-6">
          <Alert tone="success">
            Pipeline RFM ({resultat.algorithme_utilise.toUpperCase()}) · {resultat.n_clients} clients
            {resultat.k_choisi != null && ` · k=${resultat.k_choisi}`}
            {resultat.eps_choisi != null && ` · ε=${resultat.eps_choisi.toFixed(3)}`}
            {resultat.silhouette_choisi != null && ` · Sil.=${resultat.silhouette_choisi.toFixed(3)}`}
            {!resultat.ecrit_en_base && " (mode exploration — non écrit en base)"}
          </Alert>

          {resultat.avertissements.map((a, i) => (
            <Alert key={i} tone="warning">
              {a}
            </Alert>
          ))}

          {resultat.diagnostics.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Card>
                <CardTitle>
                  {resultat.algorithme_utilise === "dbscan"
                    ? "Courbe k-distance (sélection d'epsilon)"
                    : resultat.algorithme_utilise === "gmm"
                      ? "BIC (Bayesian Information Criterion)"
                      : "Méthode du coude (inertie)"}
                </CardTitle>
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={resultat.diagnostics}>
                    <CartesianGrid stroke="#E3E7EE" vertical={false} />
                    <XAxis
                      dataKey="k"
                      tick={{ fontSize: 12, fill: "#5b6478" }}
                      label={{
                        value: resultat.algorithme_utilise === "dbscan" ? "candidat (ε croissant)" : "k",
                        position: "insideBottom",
                        offset: -3,
                        fontSize: 11,
                      }}
                    />
                    <YAxis tick={{ fontSize: 12, fill: "#5b6478" }} width={40} />
                    <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 13 }} />
                    <Line type="monotone" dataKey="inertie" stroke={COULEUR_PRIMAIRE} strokeWidth={2.5} dot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </Card>
              <Card>
                <CardTitle>Score silhouette</CardTitle>
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={resultat.diagnostics}>
                    <CartesianGrid stroke="#E3E7EE" vertical={false} />
                    <XAxis dataKey="k" tick={{ fontSize: 12, fill: "#5b6478" }} label={{ value: "k", position: "insideBottom", offset: -3, fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 12, fill: "#5b6478" }} width={40} domain={[0, "auto"]} />
                    <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 13 }} />
                    <Line type="monotone" dataKey="silhouette" stroke={COULEUR_ACCENT} strokeWidth={2.5} dot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </Card>
            </div>
          )}

          {segments.length > 0 && (
            <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
              <Card>
                <CardTitle>Répartition</CardTitle>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={segments}>
                    <CartesianGrid stroke="#E3E7EE" vertical={false} />
                    <XAxis dataKey="segment_rfm" tick={{ fontSize: 11, fill: "#5b6478" }} />
                    <YAxis tick={{ fontSize: 12, fill: "#5b6478" }} width={30} />
                    <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 13 }} />
                    <Bar dataKey="nb_clients" fill={COULEUR_PRIMAIRE} radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
              <Card>
                <CardTitle>Recommandations par segment</CardTitle>
                <Table
                  keyField="segment_rfm"
                  columns={[
                    { key: "segment_rfm", header: "Segment", render: (r) => <SegmentBadge segment={r.segment_rfm} /> },
                    { key: "nb_clients", header: "Clients" },
                    {
                      key: "montant_total_moyen",
                      header: "Montant moyen",
                      render: (r) => r.montant_total_moyen.toLocaleString("fr-FR", { maximumFractionDigits: 0 }),
                    },
                    {
                      key: "score_churn_moyen",
                      header: "Churn moyen",
                      render: (r) => (r.score_churn_moyen != null ? r.score_churn_moyen.toFixed(2) : "—"),
                    },
                    { key: "action_marketing_recommandee", header: "Action recommandée" },
                  ]}
                  rows={segments}
                />
              </Card>
            </div>
          )}

          <Card>
            <CardTitle
              action={
                <div className="w-48">
                  <Select value={filtreSegment} onChange={(e) => changerFiltre(e.target.value)}>
                    <option value="Tous">Tous les segments</option>
                    {segments.map((s) => (
                      <option key={s.segment_rfm} value={s.segment_rfm}>
                        {s.segment_rfm}
                      </option>
                    ))}
                  </Select>
                </div>
              }
            >
              Explorer les clients
            </CardTitle>
            <Table
              keyField="client_id"
              emptyLabel="Aucun client pour ce filtre."
              columns={[
                { key: "code_client_externe", header: "Client" },
                { key: "segment_rfm", header: "Segment", render: (r) => <SegmentBadge segment={r.segment_rfm} /> },
                { key: "recence_jours", header: "Récence (j)" },
                { key: "frequence_achats", header: "Fréquence" },
                {
                  key: "montant_total",
                  header: "Montant total",
                  render: (r) => r.montant_total?.toLocaleString("fr-FR", { maximumFractionDigits: 0 }),
                },
                {
                  key: "score_churn",
                  header: "Score churn",
                  render: (r) => (r.score_churn != null ? r.score_churn.toFixed(2) : "—"),
                },
              ]}
              rows={clients}
            />
          </Card>
        </div>
      )}
    </div>
  );
}
