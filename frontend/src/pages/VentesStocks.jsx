import { useEffect, useState } from "react";
import { getKpis, getStockAlertes } from "../api/client";
import { formatKpiValue } from "../lib/format";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardTitle } from "../components/ui/Card";
import { KpiCard } from "../components/ui/KpiCard";
import { Alert } from "../components/ui/Alert";
import { Table } from "../components/ui/Table";
import { NiveauBadge } from "../components/ui/Badge";
import { LoadingBlock } from "../components/ui/Spinner";
import { IconTarget, IconTrendingUp, IconUpload } from "../components/icons";
import { PrevisionsVentes } from "../components/PrevisionsVentes";

export function VentesStocks() {
  const [kpis, setKpis] = useState(null);
  const [alertes, setAlertes] = useState([]);
  const [erreurInitiale, setErreurInitiale] = useState(null);

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

  const nCritiques = alertes.filter((a) => a.niveau === "critique").length;

  return (
    <div className="space-y-8">
      <PageHeader
        icon={IconTrendingUp}
        eyebrow="Module Maslaw"
        title="Ventes, stocks & trésorerie"
        subtitle="KPIs globaux, alertes de réapprovisionnement, et prévision des ventes par 3 modèles calculés automatiquement sur vos données, avec interprétation."
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
        {/* Tableau borné en hauteur : avec un vrai catalogue (130 alertes sur
            AdventureWorks), il repoussait les prévisions tout en bas de la page. */}
        <div className="max-h-96 overflow-y-auto rounded-xl">
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
        </div>
        {alertes.length > 8 && (
          <p className="mt-2 text-xs text-muted">{alertes.length} alertes — faites défiler le tableau, les plus urgentes en premier.</p>
        )}
      </Card>

      <PrevisionsVentes />
    </div>
  );
}
