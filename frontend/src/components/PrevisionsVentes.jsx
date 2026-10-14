import { useEffect, useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getComparaisonPrevisions } from "../api/client";
import { formatNumber } from "../lib/format";
import { Card, CardTitle } from "./ui/Card";
import { Alert } from "./ui/Alert";
import { Spinner } from "./ui/Spinner";

// Une couleur fixe par modèle (identité, jamais le rang) — palette catégorielle
// validée (contrastes et daltonisme) ; le nom du modèle est toujours écrit à
// côté, la couleur n'est jamais le seul repère.
export const COULEURS_MODELES = { prophet: "#2a78d6", arima: "#eb6834", xgboost: "#1baf7a" };
const COULEUR_REEL = "#8a93a3";
const HORIZONS = [14, 30, 60, 90];
const HISTORIQUE_CARTE_JOURS = 45; // cartes par modèle : plus de place pour la prévision
const AXE = { fontSize: 11, fill: "#5b6478" };

const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
const dateCourte = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });

/** Fusionne historique réel + prévisions en lignes par date pour Recharts.
 * Le dernier jour réel sert aussi de point de départ à chaque prévision,
 * pour que la courbe prévue prolonge la courbe réelle sans trou. */
function construireLignes(historique, modeles) {
  const parDate = new Map();
  const ligne = (d) => {
    if (!parDate.has(d)) parDate.set(d, { date: d });
    return parDate.get(d);
  };
  historique.forEach((p) => (ligne(p.date).reel = p.valeur));
  const dernier = historique.at(-1);
  modeles.forEach((m) => {
    if (dernier) ligne(dernier.date)[m.modele] = dernier.valeur;
    m.points.forEach((p) => {
      const l = ligne(p.date_prevision);
      l[m.modele] = p.valeur_prevue;
      if (p.borne_basse != null && p.borne_haute != null) l[`${m.modele}_bande`] = [p.borne_basse, p.borne_haute];
    });
  });
  return [...parDate.values()].sort((a, b) => a.date.localeCompare(b.date));
}

function InfoBulle({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const valeurs = payload.filter((p) => !String(p.dataKey).endsWith("_bande") && p.value != null);
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2 text-xs shadow-[var(--shadow-card)]">
      <p className="mb-1 font-semibold text-ink">{dateCourte(label)}</p>
      {valeurs.map((p) => (
        <p key={p.dataKey} className="flex items-center gap-2 text-muted">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />
          {p.name} : <span className="font-tabular text-ink">{formatNumber(p.value)}</span>
        </p>
      ))}
    </div>
  );
}

function Graphique({ lignes, modeles, hauteur, legende }) {
  return (
    <ResponsiveContainer width="100%" height={hauteur}>
      <ComposedChart data={lignes} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#E3E7EE" vertical={false} />
        <XAxis dataKey="date" tickFormatter={dateCourte} tick={AXE} minTickGap={28} />
        <YAxis tickFormatter={(v) => compact.format(v)} tick={AXE} width={48} />
        <Tooltip content={<InfoBulle />} />
        {legende && <Legend iconType="plainline" itemSorter={null} wrapperStyle={{ fontSize: 12 }} />}
        {modeles.map((m) => (
          <Area
            key={`${m.modele}_bande`}
            dataKey={`${m.modele}_bande`}
            name={`Intervalle ${m.nom}`}
            stroke="none"
            fill={COULEURS_MODELES[m.modele]}
            fillOpacity={0.14}
            legendType="none"
            isAnimationActive={false}
          />
        ))}
        <Line dataKey="reel" name="CA réel" stroke={COULEUR_REEL} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        {modeles.map((m) => (
          <Line
            key={m.modele}
            dataKey={m.modele}
            name={m.nom}
            stroke={COULEURS_MODELES[m.modele]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

const FIABILITE = {
  bonne: "bg-success-bg text-success",
  moyenne: "bg-warning-bg text-accent-dark",
  faible: "bg-danger-bg text-danger",
};

function CarteModele({ modele, historique, meilleur }) {
  const lignes = useMemo(
    () => construireLignes(historique.slice(-HISTORIQUE_CARTE_JOURS), [modele]),
    [historique, modele],
  );
  const fiabilite = modele.indicateurs?.fiabilite;
  return (
    <article
      className={`flex min-w-0 flex-col rounded-2xl border bg-surface p-4 ${meilleur ? "border-brand" : "border-line"}`}
      data-testid={`prevision-${modele.modele}`}
    >
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: COULEURS_MODELES[modele.modele] }} />
        <h3 className="font-display text-sm font-bold text-ink">{modele.nom}</h3>
        {meilleur && (
          <span className="rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-white">★ Le plus fiable</span>
        )}
        {fiabilite && (
          <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-medium ${FIABILITE[fiabilite]}`}>
            Fiabilité {fiabilite}
          </span>
        )}
      </header>

      {modele.statut === "erreur" ? (
        <Alert tone="danger">Ce modèle n'a pas pu être calculé : {modele.erreur}</Alert>
      ) : (
        <>
          <Graphique lignes={lignes} modeles={[modele]} hauteur={190} />
          <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
            {[
              ["CA prévu", modele.indicateurs?.total_prevu],
              ["RMSE", modele.rmse_validation],
              ["MAE", modele.mae_validation],
            ].map(([libelle, valeur]) => (
              <div key={libelle} className="rounded-lg bg-paper px-2 py-1.5">
                <dt className="text-[11px] text-muted">{libelle}</dt>
                <dd className="font-tabular text-sm font-semibold text-ink">
                  {valeur == null ? "—" : compact.format(valeur)}
                </dd>
              </div>
            ))}
          </dl>
          <ul className="mt-3 space-y-1.5 text-[13px] leading-relaxed text-ink">
            {modele.interpretation.map((phrase) => (
              <li key={phrase} className="flex gap-2">
                <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-muted" />
                <span>{phrase}</span>
              </li>
            ))}
          </ul>
          {modele.avertissements.length > 0 && (
            <details className="mt-2 text-xs text-muted">
              <summary className="cursor-pointer select-none hover:text-ink">
                {modele.avertissements.length} remarque(s) méthodologique(s)
              </summary>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {modele.avertissements.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </article>
  );
}

/** Prévision des ventes : les 3 modèles sont calculés automatiquement côté
 * serveur (en parallèle, en arrière-plan et mis en cache), sans choix manuel.
 * Affiche une synthèse, un graphique comparatif, puis un graphique interprété
 * par modèle. */
export function PrevisionsVentes() {
  const [horizon, setHorizon] = useState(30);
  const [resultat, setResultat] = useState(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState(null);

  useEffect(() => {
    let annule = false;
    setChargement(true);
    setErreur(null);
    getComparaisonPrevisions(horizon)
      .then((r) => !annule && setResultat(r))
      .catch((e) => !annule && setErreur(e.message))
      .finally(() => !annule && setChargement(false));
    return () => {
      annule = true;
    };
  }, [horizon]);

  const modelesOk = resultat?.modeles.filter((m) => m.statut === "ok") ?? [];
  const lignesComparaison = useMemo(
    () => (resultat ? construireLignes(resultat.historique, modelesOk) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [resultat],
  );
  const meilleur = resultat?.synthese.meilleur_modele;

  const selecteurHorizon = (
    <div className="flex items-center gap-1 rounded-xl bg-paper p-1" role="group" aria-label="Horizon de prévision">
      {HORIZONS.map((h) => (
        <button
          key={h}
          type="button"
          onClick={() => setHorizon(h)}
          aria-pressed={h === horizon}
          className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
            h === horizon ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
          }`}
        >
          {h} j
        </button>
      ))}
    </div>
  );

  return (
    <Card>
      <CardTitle action={selecteurHorizon}>🔮 Prévision des ventes — Prophet, ARIMA et XGBoost</CardTitle>

      {erreur && <Alert tone="danger">{erreur}</Alert>}

      {chargement && (
        <div className="flex items-center gap-3 rounded-xl bg-paper px-4 py-3 text-sm text-muted" role="status">
          <Spinner size={16} className="text-brand" />
          Calcul des 3 modèles en arrière-plan (entraînement, validation, prévision à {horizon} jours)… Le reste de la
          page reste utilisable.
        </div>
      )}

      {!chargement && resultat && resultat.modeles.length === 0 && (
        <p className="text-sm text-muted">{resultat.synthese.texte[0]}</p>
      )}

      {!chargement && resultat && resultat.modeles.length > 0 && (
        <div className="space-y-6">
          <section className="rounded-xl border border-line bg-paper p-4" data-testid="synthese-previsions">
            <h3 className="mb-2 text-sm font-semibold text-ink">Synthèse</h3>
            <ul className="space-y-1.5 text-[13px] leading-relaxed text-ink">
              {resultat.synthese.texte.map((t) => (
                <li key={t} className="flex gap-2">
                  <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-brand" />
                  <span>{t}</span>
                </li>
              ))}
            </ul>
            <div className="mt-4">
              <Graphique lignes={lignesComparaison} modeles={modelesOk} hauteur={280} legende />
            </div>
            <p className="mt-1 text-[11px] text-muted">
              Gris : CA réel des {resultat.historique.length} derniers jours · zones colorées : intervalles de confiance ·
              calculé le {new Date(resultat.calcule_le + "Z").toLocaleString("fr-FR")}
            </p>
          </section>

          <div className="grid gap-4 xl:grid-cols-3">
            {resultat.modeles.map((m) => (
              <CarteModele key={m.modele} modele={m} historique={resultat.historique} meilleur={m.modele === meilleur} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
