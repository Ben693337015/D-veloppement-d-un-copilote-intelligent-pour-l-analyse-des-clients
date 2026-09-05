import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getKpis, getPrevisionVentes, getStockAlertes } from "../api/client";
import { formatKpiValue } from "../lib/format";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardTitle } from "../components/ui/Card";
import { KpiCard } from "../components/ui/KpiCard";
import { Button } from "../components/ui/Button";
import { Alert } from "../components/ui/Alert";
import { Select } from "../components/ui/Select";
import { Table } from "../components/ui/Table";
import { NiveauBadge } from "../components/ui/Badge";
import { LoadingBlock } from "../components/ui/Spinner";
import { IconAlert, IconTarget, IconTrendingUp, IconUpload } from "../components/icons";

const COULEUR_PRIMAIRE = "#2f5d8a";

export function VentesStocks() {
  const [kpis, setKpis] = useState(null);
  const [alertes, setAlertes] = useState([]);
  const [erreurInitiale, setErreurInitiale] = useState(null);

  const [modele, setModele] = useState("prophet");
  const [horizon, setHorizon] = useState(30);
  const [chargementPrevision, setChargementPrevision] = useState(false);
  const [prevision, setPrevision] = useState(null);
  const [erreurPrevision, setErreurPrevision] = useState(null);

  useEffect(() => {
    let annule = false;
    Promise.all([getKpis(), getStockAlertes()])
      .then(([k, a]) => {
        if (annule) return;
        setKpis(k);
        setAlertes(a);
      })
      .catch((err) => !annule && setErreurInitiale(err.message));
    return () => {
      annule = true;
    };
  }, []);

  async function genererPrevision() {
    setChargementPrevision(true);
    setErreurPrevision(null);
    try {
      const data = await getPrevisionVentes({ horizonJours: Number(horizon), modele });
      setPrevision(data);
    } catch (err) {
      setErreurPrevision(err.message);
    } finally {
      setChargementPrevision(false);
    }
  }

  const nCritiques = alertes.filter((a) => a.niveau === "critique").length;

  return (
    <div className="space-y-8">
      <PageHeader
        icon={IconTrendingUp}
        eyebrow="Module Maslaw"
        title="Ventes, stocks & trésorerie"
        subtitle="KPIs globaux, alertes de réapprovisionnement, prévision des ventes entraînée en ligne sur vos données."
      />

      {erreurInitiale && <Alert tone="danger">{erreurInitiale}</Alert>}

      {!kpis && !erreurInitiale && <LoadingBlock label="Chargement des KPIs…" />}

      {kpis &&
        (() => {
          const clients = formatKpiValue(kpis.nb_clients);
          const transactions = formatKpiValue(kpis.nb_transactions);
          const chiffreAffaires = formatKpiValue(kpis.chiffre_affaires_total);
          const stock = formatKpiValue(kpis.stock_total_unites);
          const tresorerie = formatKpiValue(kpis.solde_tresorerie_estime);
          return (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
              <KpiCard icon={IconTarget} label="Clients" value={clients.display} hint={clients.full} />
              <KpiCard
                icon={IconTrendingUp}
                label="Transactions"
                value={transactions.display}
                hint={transactions.full}
              />
              <KpiCard
                icon={IconTrendingUp}
                label="Chiffre d'affaires"
                value={chiffreAffaires.display}
                hint={chiffreAffaires.full}
                tone="accent"
              />
              <KpiCard icon={IconUpload} label="Stock (unités)" value={stock.display} hint={stock.full} />
              <KpiCard
                icon={IconTrendingUp}
                label="Solde trésorerie"
                value={tresorerie.display}
                hint={tresorerie.full}
                tone={kpis.solde_tresorerie_estime < 0 ? "danger" : "success"}
              />
            </div>
          );
        })()}

      <Card>
        <CardTitle>🔔 Alertes de stock</CardTitle>
        {nCritiques > 0 && (
          <Alert tone="danger" className="mb-4">
            {nCritiques} produit(s) en niveau <strong>critique</strong> — réapprovisionnement urgent.
          </Alert>
        )}
        <Table
          keyField="code_produit"
          emptyLabel="Aucune alerte de stock active."
          columns={[
            { key: "code_produit", header: "Code produit" },
            { key: "nom_produit", header: "Nom" },
            { key: "quantite_disponible", header: "Stock disponible" },
            { key: "seuil_alerte", header: "Seuil d'alerte" },
            { key: "niveau", header: "Niveau", render: (r) => <NiveauBadge niveau={r.niveau} /> },
          ]}
          rows={alertes}
        />
      </Card>

      <Card>
        <CardTitle>🔮 Prévision des ventes</CardTitle>
        <div className="flex flex-wrap items-end gap-4">
          <div className="w-40">
            <Select label="Modèle" value={modele} onChange={(e) => setModele(e.target.value)}>
              <option value="prophet">Prophet</option>
              <option value="arima">ARIMA</option>
              <option value="xgboost">XGBoost</option>
            </Select>
          </div>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink">Horizon (jours)</span>
            <input
              type="number"
              min={1}
              max={90}
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              className="w-28 rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
            />
          </label>
          <Button onClick={genererPrevision} loading={chargementPrevision}>
            Générer la prévision
          </Button>
        </div>

        {erreurPrevision && <Alert tone="danger" className="mt-4">{erreurPrevision}</Alert>}

        {prevision && (
          <div className="mt-6">
            <p className="mb-3 text-sm text-muted">
              Modèle utilisé : <strong className="text-ink">{prevision.modele_utilise}</strong>
            </p>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={prevision.points}>
                <CartesianGrid stroke="#E3E7EE" vertical={false} />
                <XAxis dataKey="date_prevision" tick={{ fontSize: 11, fill: "#5b6478" }} />
                <YAxis tick={{ fontSize: 12, fill: "#5b6478" }} width={50} />
                <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 13 }} />
                <Line type="monotone" dataKey="valeur_prevue" stroke={COULEUR_PRIMAIRE} strokeWidth={2.5} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>

            {prevision.rmse_validation != null && (
              <div className="mt-4 grid grid-cols-2 gap-4 sm:w-72">
                <div className="rounded-xl bg-paper px-4 py-3">
                  <p className="text-xs text-muted">RMSE (validation)</p>
                  <p className="font-tabular text-lg font-semibold text-ink">
                    {prevision.rmse_validation.toFixed(2)}
                  </p>
                </div>
                <div className="rounded-xl bg-paper px-4 py-3">
                  <p className="text-xs text-muted">MAE (validation)</p>
                  <p className="font-tabular text-lg font-semibold text-ink">
                    {prevision.mae_validation.toFixed(2)}
                  </p>
                </div>
              </div>
            )}

            {prevision.avertissements.map((a, i) => (
              <Alert key={i} tone="warning" className="mt-3">
                {a}
              </Alert>
            ))}
          </div>
        )}

        {!prevision && !chargementPrevision && (
          <p className="mt-6 flex items-center gap-2 text-sm text-muted">
            <IconAlert width={15} height={15} />
            Choisissez un modèle et un horizon, puis cliquez sur « Générer la prévision ».
          </p>
        )}
      </Card>
    </div>
  );
}
