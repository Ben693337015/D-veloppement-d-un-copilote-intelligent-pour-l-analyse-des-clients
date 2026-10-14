import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardTitle } from "./ui/Card";

// Couleur fixe par algorithme (identité, jamais le rang) — palette catégorielle
// validée (contrastes, daltonisme) ; le nom est toujours écrit à côté.
export const COULEURS_ALGOS = { kmeans: "#2a78d6", gmm: "#eb6834", dbscan: "#1baf7a" };
const AXE = { fontSize: 11, fill: "#5b6478" };
const f3 = (v) => (v == null ? "—" : v.toFixed(3).replace(".", ","));
const f2 = (v) => (v == null ? "—" : v.toFixed(2).replace(".", ","));
const pct = (v) => `${(v * 100).toFixed(v > 0 && v < 0.01 ? 1 : 0).replace(".", ",")} %`;
const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });

// Échelle de Kaufman & Rousseeuw (1990) pour lire la silhouette.
const BANDES = [
  { y1: 0, y2: 0.25, label: "non substantielle" },
  { y1: 0.25, y2: 0.5, label: "faible" },
  { y1: 0.5, y2: 0.7, label: "raisonnable" },
  { y1: 0.7, y2: 1, label: "forte" },
];

function Statut({ algo }) {
  if (algo.rang === 1)
    return <span className="whitespace-nowrap rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-white">★ Recommandé</span>;
  if (algo.eligible)
    return <span className="rounded-full bg-paper px-2 py-0.5 text-[11px] font-medium text-muted">{algo.rang}ᵉ</span>;
  return <span className="rounded-full bg-danger-bg px-2 py-0.5 text-[11px] font-medium text-danger">Écarté</span>;
}

function parametre(algo) {
  if (algo.k_choisi != null) return `k = ${algo.k_choisi}`;
  if (algo.eps_choisi != null) return `ε = ${f3(algo.eps_choisi)}`;
  return "—";
}

/** Courbe de diagnostic : silhouette selon k (K-Means, GMM) ou selon le
 * candidat ε testé (DBSCAN), avec le point retenu mis en évidence. */
function CourbeDiagnostic({ algo }) {
  const dbscan = algo.algorithme === "dbscan";
  const points = algo.diagnostics
    .filter((d) => d.silhouette != null)
    .map((d) => ({ x: dbscan ? Number(d.inertie.toFixed(3)) : d.k, silhouette: d.silhouette, groupes: d.n_clusters_trouves }));
  const retenu = dbscan
    ? points.reduce((a, b) => (Math.abs(b.x - algo.eps_choisi) < Math.abs(a.x - algo.eps_choisi) ? b : a), points[0])
    : points.find((p) => p.x === algo.k_choisi);
  if (points.length === 0) return <p className="text-xs text-muted">Aucun point de diagnostic exploitable.</p>;
  return (
    <ResponsiveContainer width="100%" height={170}>
      <LineChart data={points} margin={{ top: 10, right: 12, bottom: 14, left: 0 }}>
        <CartesianGrid stroke="#E3E7EE" vertical={false} />
        <XAxis
          dataKey="x"
          tick={AXE}
          label={{ value: dbscan ? "epsilon testé" : "nombre de groupes k", position: "insideBottom", offset: -8, fontSize: 11, fill: "#5b6478" }}
        />
        <YAxis tick={AXE} width={40} domain={[0, "auto"]} tickFormatter={(v) => v.toFixed(2)} />
        <Tooltip
          formatter={(v, n, p) => [f3(v), dbscan && p.payload.groupes != null ? `silhouette (${p.payload.groupes} groupes)` : "silhouette"]}
          labelFormatter={(l) => (dbscan ? `ε = ${l}` : `k = ${l}`)}
          contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 12 }}
        />
        <Line dataKey="silhouette" stroke={COULEURS_ALGOS[algo.algorithme]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
        {retenu && (
          <ReferenceDot x={retenu.x} y={retenu.silhouette} r={6} fill={COULEURS_ALGOS[algo.algorithme]} stroke="#fff" strokeWidth={2} />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

function CarteAlgo({ algo }) {
  return (
    <article
      className={`flex min-w-0 flex-col rounded-2xl border bg-surface p-4 ${algo.rang === 1 ? "border-brand" : "border-line"}`}
      data-testid={`algo-${algo.algorithme}`}
    >
      <header className="mb-1 flex flex-wrap items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: COULEURS_ALGOS[algo.algorithme] }} />
        <h3 className="font-display text-sm font-bold text-ink">{algo.nom}</h3>
        <Statut algo={algo} />
        <span className="ml-auto font-tabular text-xs text-muted">{parametre(algo)}</span>
      </header>
      {algo.motif_exclusion && <p className="mb-2 text-xs text-danger">Écarté : {algo.motif_exclusion}.</p>}
      {algo.statut === "ok" && (
        <>
          <p className="mb-1 text-[11px] text-muted">Silhouette selon le paramètre testé (point plein : valeur retenue)</p>
          <CourbeDiagnostic algo={algo} />
          <ul className="mt-2 space-y-1.5 text-[13px] leading-relaxed text-ink">
            {algo.interpretation.map((phrase) => (
              <li key={phrase} className="flex gap-2">
                <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-muted" />
                <span>{phrase}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

/** Mode « auto » du pipeline RFM : synthèse, comparaison chiffrée et une
 * carte interprétée par algorithme. */
export function ComparaisonSegmentation({ resultat }) {
  const algos = resultat.comparaison;
  const barres = algos
    .filter((a) => a.silhouette != null)
    .map((a) => ({ nom: a.nom, algorithme: a.algorithme, silhouette: a.silhouette, eligible: a.eligible }));
  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Évaluation automatique des 3 algorithmes</CardTitle>
        <ul className="mb-5 space-y-1.5 text-[13px] leading-relaxed text-ink" data-testid="synthese-segmentation">
          {resultat.synthese.map((t) => (
            <li key={t} className="flex gap-2">
              <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-brand" />
              <span>{t}</span>
            </li>
          ))}
        </ul>

        <div className="grid gap-6 2xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="min-w-0 overflow-x-auto">
            <table className="w-full min-w-[620px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted [&_th]:whitespace-nowrap">
                  <th className="py-2 pr-3 font-semibold">Algorithme</th>
                  <th className="py-2 pr-3 font-semibold">Paramètre</th>
                  <th className="py-2 pr-3 text-right font-semibold" title="plus haut = mieux">Silhouette ↑</th>
                  <th className="py-2 pr-3 text-right font-semibold" title="plus bas = mieux">Davies-Bouldin ↓</th>
                  <th className="py-2 pr-3 text-right font-semibold" title="plus haut = mieux">Calinski-Harabasz ↑</th>
                  <th className="py-2 pr-3 text-right font-semibold">Groupes</th>
                  <th className="py-2 font-semibold">Statut</th>
                </tr>
              </thead>
              <tbody className="font-tabular [&_td]:whitespace-nowrap">
                {algos.map((a) => (
                  <tr key={a.algorithme} className="border-b border-line/60 last:border-0">
                    <td className="py-2 pr-3 font-sans font-medium text-ink">
                      <span className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: COULEURS_ALGOS[a.algorithme] }} />
                      {a.nom}
                    </td>
                    <td className="py-2 pr-3 text-muted">{parametre(a)}</td>
                    <td className="py-2 pr-3 text-right text-ink">{f3(a.silhouette)}</td>
                    <td className="py-2 pr-3 text-right text-ink">{f2(a.davies_bouldin)}</td>
                    <td className="py-2 pr-3 text-right text-ink">{a.calinski_harabasz == null ? "—" : compact.format(a.calinski_harabasz)}</td>
                    <td className="py-2 pr-3 text-right text-ink">
                      {a.n_groupes}
                      {a.part_bruit > 0 && <span className="text-muted"> + {pct(a.part_bruit)} bruit</span>}
                    </td>
                    <td className="py-2"><Statut algo={a} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-muted">
              Choix : algorithmes exploitables (2 à 8 groupes, ≤ 10 % de bruit), classés par rang moyen sur les
              3 métriques. Calculées sur les mêmes données RFM prétraitées.
            </p>
          </div>

          <div className="min-w-0">
            <p className="mb-1 text-xs font-medium text-ink">Silhouette sur l'échelle de Kaufman & Rousseeuw</p>
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={barres} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
                {BANDES.map((b, i) => (
                  <ReferenceArea key={b.label} x1={b.y1} x2={b.y2} fill={i % 2 ? "#f5f6f9" : "#ffffff"} ifOverflow="hidden"
                    label={{ value: b.label, position: "insideTop", fontSize: 10, fill: "#8a93a3" }} />
                ))}
                <XAxis type="number" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.7, 1]} tick={AXE} />
                <YAxis type="category" dataKey="nom" tick={AXE} width={64} />
                <Tooltip formatter={(v) => [f3(v), "silhouette"]} contentStyle={{ borderRadius: 12, borderColor: "#E3E7EE", fontSize: 12 }} />
                <Bar dataKey="silhouette" barSize={14} radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {barres.map((b) => (
                    <Cell key={b.algorithme} fill={COULEURS_ALGOS[b.algorithme]} fillOpacity={b.eligible ? 1 : 0.35} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        {[...algos]
          .sort((a, b) => (a.rang ?? 9) - (b.rang ?? 9))
          .map((a) => (
            <CarteAlgo key={a.algorithme} algo={a} />
          ))}
      </div>
    </div>
  );
}
