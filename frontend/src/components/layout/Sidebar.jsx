import { NavLink, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import logo from "../../assets/logo.jpg";
import { useApiHealth } from "../../hooks/useApiHealth";
import { useDatasetStatus } from "../../hooks/useDatasetStatus";
import { formatNumber } from "../../lib/format";
import { getCurrentUser, logout } from "../../api/client";
import {
  IconChat,
  IconDoc,
  IconHome,
  IconLock,
  IconLogout,
  IconTarget,
  IconTrendingUp,
  IconUpload,
} from "../icons";

// `requiresData: true` = page inutilisable tant qu'aucun jeu de données
// n'a été importé (elle interrogerait des indicateurs vides) — grisée et
// non cliquable dans ce cas, plutôt que de laisser l'utilisateur tomber
// sur une page vide sans explication.
const LIENS = [
  { to: "/", label: "Accueil", icon: IconHome, end: true, requiresData: false },
  { to: "/donnees", label: "Données", icon: IconUpload, requiresData: false },
  { to: "/segmentation", label: "Segmentation RFM", icon: IconTarget, requiresData: true },
  { to: "/ventes", label: "Ventes & Stocks", icon: IconTrendingUp, requiresData: true },
  { to: "/copilote", label: "Copilote", icon: IconChat, requiresData: false },
  { to: "/rapport", label: "Rapport", icon: IconDoc, requiresData: true },
];

export function Sidebar({ open, onNavigate, onDeconnecte }) {
  const online = useApiHealth();
  const { status: dataStatus, kpis } = useDatasetStatus();
  const [utilisateur, setUtilisateur] = useState(null);
  const navigate = useNavigate();
  // Tant qu'on ne sait pas ("loading"/"unknown"), on ne bloque personne —
  // seul un "empty" confirmé désactive les pages qui ont besoin de données.
  const dataLoaded = dataStatus !== "empty";

  useEffect(() => {
    let annule = false;
    getCurrentUser()
      .then((u) => !annule && setUtilisateur(u))
      .catch(() => {}); // pas grave si ça échoue : c'est juste pour l'affichage
    return () => {
      annule = true;
    };
  }, []);

  function seDeconnecter() {
    logout();
    onDeconnecte?.();
    navigate("/connexion", { replace: true });
  }

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-line bg-surface text-ink transition-transform duration-200 lg:translate-x-0 ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex items-center gap-3 border-b border-line px-6 py-6">
        <div className="focus-ring shrink-0">
          <img
            src={logo}
            alt="Copilote IA PME"
            className="h-11 w-11 rounded-full object-cover ring-2 ring-brand-light"
          />
        </div>
        <div className="min-w-0">
          <p className="font-display text-[15px] font-extrabold leading-tight tracking-tight text-ink">
            Copilote IA PME
          </p>
          <p className="truncate text-xs text-muted">Groupe 2 · Master IA Appliquée</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {LIENS.map(({ to, label, icon: Icon, end, requiresData }) => {
          const desactive = requiresData && !dataLoaded;

          if (desactive) {
            return (
              <span
                key={to}
                title="Importez d'abord un jeu de données pour activer cette page"
                aria-disabled="true"
                className="flex cursor-not-allowed items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-muted/40"
              >
                <Icon className="text-muted/40" />
                <span className="flex-1">{label}</span>
                <IconLock className="text-muted/40" />
              </span>
            );
          }

          return (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onNavigate}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-brand-light text-brand"
                    : "text-muted hover:bg-paper hover:text-ink"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={isActive ? "text-brand" : "text-muted"} />
                  {label}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="space-y-2 border-t border-line px-4 py-4">
        {utilisateur && (
          <div className="flex items-center justify-between gap-2 rounded-xl px-3.5 py-2 text-xs">
            <span className="truncate text-muted" title={utilisateur.email}>
              {utilisateur.nom || utilisateur.email}
            </span>
            <button
              type="button"
              onClick={seDeconnecter}
              title="Se déconnecter"
              className="focus-ring flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-muted hover:bg-paper hover:text-ink"
            >
              <IconLogout width={14} height={14} />
              <span>Déconnexion</span>
            </button>
          </div>
        )}

        <div className="flex items-center gap-2 rounded-xl bg-paper px-3.5 py-2.5 text-xs">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              online === null ? "animate-pulse bg-muted/40" : online ? "bg-emerald-500" : "bg-red-500"
            }`}
            aria-hidden="true"
          />
          <span className="text-muted">
            {online === null ? "Vérification…" : online ? "API connectée" : "API injoignable"}
          </span>
        </div>

        <div className="flex items-center gap-2 rounded-xl bg-paper px-3.5 py-2.5 text-xs">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              dataStatus === "loaded" ? "bg-emerald-500" : dataStatus === "empty" ? "bg-amber-500" : "animate-pulse bg-muted/40"
            }`}
            aria-hidden="true"
          />
          <span className="truncate text-muted">
            {dataStatus === "loading" && "Vérification des données…"}
            {dataStatus === "unknown" && "Statut des données inconnu"}
            {dataStatus === "empty" && "Aucune donnée chargée"}
            {dataStatus === "loaded" && kpis && `${formatNumber(kpis.nb_clients)} clients chargés`}
          </span>
        </div>
      </div>
    </aside>
  );
}
