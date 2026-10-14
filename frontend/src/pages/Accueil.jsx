import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import logo from "../assets/logo.jpg";
import { getKpis, getStockAlertes } from "../api/client";
import { formatKpiValue } from "../lib/format";
import { Card } from "../components/ui/Card";
import { KpiCard } from "../components/ui/KpiCard";
import { LoadingBlock } from "../components/ui/Spinner";
import { Alert } from "../components/ui/Alert";
import {
  IconAlert,
  IconChat,
  IconDoc,
  IconTarget,
  IconTrendingUp,
  IconUpload,
} from "../components/icons";

const ETAPES = [
  {
    n: "01",
    titre: "Importez vos données",
    texte: "Un export de caisse, d'ERP ou un simple tableur — quel que soit le nom de vos colonnes.",
  },
  {
    n: "02",
    titre: "Prétraitement automatique",
    texte: "Nettoyage, détection des retours, déduplication, plafonnement des valeurs extrêmes.",
  },
  {
    n: "03",
    titre: "Lancez les deux modules",
    texte: "Segmentation client (RFM) et pilotage ventes/stocks/trésorerie, sur les mêmes données.",
  },
];

const RACCOURCIS = [
  { to: "/donnees", icon: IconUpload, titre: "Données", texte: "Importer un nouveau jeu de données" },
  { to: "/segmentation", icon: IconTarget, titre: "Segmentation RFM", texte: "Clients fidèles, à risque, occasionnels" },
  { to: "/ventes", icon: IconTrendingUp, titre: "Ventes & Stocks", texte: "KPIs, alertes, prévision des ventes" },
  { to: "/copilote", icon: IconChat, titre: "Copilote", texte: "Poser une question sur vos indicateurs" },
  { to: "/rapport", icon: IconDoc, titre: "Rapport", texte: "Générer le rapport PDF final" },
];

export function Accueil() {
  const [kpis, setKpis] = useState(null);
  const [nbAlertes, setNbAlertes] = useState(null);
  const [erreur, setErreur] = useState(null);

  useEffect(() => {
    let annule = false;
    Promise.all([getKpis(), getStockAlertes()])
      .then(([k, alertes]) => {
        if (annule) return;
        setKpis(k);
        setNbAlertes(alertes.length);
      })
      .catch((err) => !annule && setErreur(err.message));
    return () => {
      annule = true;
    };
  }, []);

  return (
    <div className="space-y-10">
      {/* Bannière */}
      <div className="relative overflow-hidden rounded-3xl bg-brand-dark px-6 py-10 text-white sm:px-10 sm:py-12">
        <div
          className="pointer-events-none absolute -right-24 -top-24 h-96 w-96 rounded-full bg-accent/25 blur-3xl"
          aria-hidden="true"
        />
        <div className="relative flex flex-col items-center gap-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-xl">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-accent">
              Copilote IA PME — Groupe 2
            </p>
            <h1 className="font-display text-3xl font-extrabold leading-tight sm:text-4xl">
              Trouvez vos meilleurs clients dans la foule de vos données.
            </h1>
            <p className="mt-4 text-[15px] leading-relaxed text-white/75">
              Une seule plateforme pour segmenter vos clients (RFM), piloter vos ventes, vos stocks
              et votre trésorerie — quel que soit le fichier que vous importez.
            </p>
            <Link
              to="/donnees"
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent-dark"
            >
              Importer mes données
            </Link>
          </div>
          <div className="focus-ring shrink-0">
            <img
              src={logo}
              alt="Illustration : isoler le bon client parmi ses clients"
              className="h-40 w-40 rounded-full object-cover ring-4 ring-white/15 sm:h-48 sm:w-48"
            />
          </div>
        </div>
      </div>

      {erreur && (
        <Alert tone="danger">
          Impossible de charger les indicateurs : {erreur}
        </Alert>
      )}

      {/* KPIs */}
      {kpis ? (
        kpis.nb_clients === 0 ? (
          <Alert tone="info">
            👋 Aucune donnée importée pour le moment — commencez par la page{" "}
            <Link to="/donnees" className="font-semibold underline underline-offset-2">
              Données
            </Link>
            .
          </Alert>
        ) : (
          (() => {
            const clients = formatKpiValue(kpis.nb_clients);
            const transactions = formatKpiValue(kpis.nb_transactions);
            const chiffreAffaires = formatKpiValue(kpis.chiffre_affaires_total);
            const stock = formatKpiValue(kpis.stock_total_unites);
            return (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                <KpiCard icon={IconTarget} label="Clients" value={clients.display} hint={clients.full} tone="brand" />
                <KpiCard
                  icon={IconTrendingUp}
                  label="Transactions"
                  value={transactions.display}
                  hint={transactions.full}
                  tone="brand"
                />
                <KpiCard
                  icon={IconTrendingUp}
                  label="Chiffre d'affaires"
                  value={chiffreAffaires.display}
                  hint={chiffreAffaires.full}
                  tone="accent"
                />
                <KpiCard
                  icon={IconUpload}
                  label="Stock (unités)"
                  value={stock.display}
                  hint={stock.full}
                  tone="brand"
                />
                <KpiCard
                  icon={IconAlert}
                  label="Alertes stock"
                  value={nbAlertes ?? "—"}
                  tone={nbAlertes > 0 ? "danger" : "success"}
                />
              </div>
            );
          })()
        )
      ) : !erreur ? (
        <LoadingBlock label="Chargement des indicateurs…" />
      ) : null}

      {/* Parcours en 3 étapes */}
      <div>
        <h2 className="font-display mb-4 text-lg font-bold text-ink">Comment ça marche</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {ETAPES.map((etape) => (
            <Card key={etape.n}>
              <span className="font-tabular text-2xl font-semibold text-accent">{etape.n}</span>
              <p className="font-display mt-2 text-[15px] font-bold text-ink">{etape.titre}</p>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{etape.texte}</p>
            </Card>
          ))}
        </div>
      </div>

      {/* Accès rapides */}
      <div>
        <h2 className="font-display mb-4 text-lg font-bold text-ink">Accès rapides</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {RACCOURCIS.map(({ to, icon: Icon, titre, texte }) => (
            <Link
              key={to}
              to={to}
              className="group flex items-start gap-3.5 rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)] transition-all hover:-translate-y-0.5 hover:shadow-[var(--shadow-pop)]"
            >
              <span className="focus-ring flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-light text-brand transition-colors group-hover:bg-brand group-hover:text-white">
                <Icon width={18} height={18} />
              </span>
              <div>
                <p className="font-display text-[15px] font-bold text-ink">{titre}</p>
                <p className="mt-0.5 text-sm text-muted">{texte}</p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
