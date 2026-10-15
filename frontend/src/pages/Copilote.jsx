import { useEffect, useRef, useState } from "react";
import { lireBriefing, lireFournisseurLlm, poserQuestion } from "../api/client";
import { Markdown } from "../components/Markdown";
import { Spinner } from "../components/ui/Spinner";
import { IconChat, IconSend } from "../components/icons";

// Origine de chaque réponse (champ `mode` de POST /copilot/chat) : l'utilisateur
// doit savoir s'il lit un texte rédigé par un LLM ou la synthèse de secours.
const ORIGINE = {
  llm: "Réponse rédigée par le LLM",
  local: "Synthèse factuelle locale — aucun LLM configuré",
  secours: "LLM indisponible — synthèse factuelle de secours",
};

// Exemples affichés quand la conversation est vide : ils montrent les trois
// capacités du copilote — lire les données, calculer, recommander.
const EXEMPLES = [
  "Quel est mon chiffre d'affaires par pays ?",
  "Combien vais-je vendre dans les 60 prochains jours ?",
  "Et si mes ventes baissaient de 15 % le mois prochain ?",
  "Quels produits dois-je commander, et en quelle quantité ?",
];

// Messages renvoyés au serveur comme mémoire de conversation (cf. copilot_service).
const MEMOIRE_MESSAGES = 6;

const STYLE_NIVEAU = {
  urgent: { pastille: "bg-danger", libelle: "Urgent", texte: "text-danger" },
  attention: { pastille: "bg-accent", libelle: "À surveiller", texte: "text-accent-dark" },
  info: { pastille: "bg-brand", libelle: "Info", texte: "text-brand" },
};

/** Paramètres d'un outil en clair (« horizon : 60 jours · variation : −15 % »). */
function decrireParametres(p = {}) {
  const morceaux = [];
  if (p.horizon_jours != null) morceaux.push(`horizon : ${p.horizon_jours} jours`);
  if (p.variation_pct != null) morceaux.push(`variation : ${p.variation_pct > 0 ? "+" : ""}${p.variation_pct} %`);
  if (p.seuil_critique) morceaux.push(`seuil : ${p.seuil_critique.toLocaleString("fr-FR")}`);
  if (p.jours_couverture != null) morceaux.push(`stock visé : ${p.jours_couverture} jours`);
  if (p.code_produit) morceaux.push(`produit : ${p.code_produit}`);
  return morceaux.join(" · ");
}

/** Briefing proactif : ce qui demande l'attention, sans attendre une question. */
function Briefing({ points, onQuestion, desactive }) {
  if (points === null) return null;
  return (
    <section className="rounded-2xl border border-line bg-paper p-4" data-testid="briefing">
      <h2 className="mb-2 text-sm font-semibold text-ink">
        {points.length
          ? `${points.length} point${points.length > 1 ? "s" : ""} demande${points.length > 1 ? "nt" : ""} votre attention`
          : "Rien d'urgent à signaler aujourd'hui"}
      </h2>
      {points.length === 0 ? (
        <p className="text-[13px] text-muted">Stocks, trésorerie, ventes et clients : aucun signal d'alerte.</p>
      ) : (
        <ul className="grid gap-2 lg:grid-cols-2">
          {points.map((p) => {
            const style = STYLE_NIVEAU[p.niveau] ?? STYLE_NIVEAU.info;
            return (
              <li key={p.titre} className="flex flex-col rounded-xl border border-line bg-surface p-3">
                <p className="flex items-center gap-2 text-[13px] font-semibold text-ink">
                  <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${style.pastille}`} />
                  <span className={`text-[11px] font-medium uppercase tracking-wide ${style.texte}`}>{style.libelle}</span>
                  {p.titre}
                </p>
                <p className="mt-1 flex-1 text-[12.5px] leading-snug text-muted">{p.detail}</p>
                <button
                  type="button"
                  disabled={desactive}
                  onClick={() => onQuestion(p.question)}
                  className="mt-2 self-start text-[12.5px] font-medium text-brand hover:underline disabled:opacity-40"
                >
                  Demander au copilote →
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function Copilote() {
  const [messages, setMessages] = useState([]);
  const [saisie, setSaisie] = useState("");
  const [chargement, setChargement] = useState(false);
  const [llm, setLlm] = useState(null);
  const [briefing, setBriefing] = useState(null);
  const finRef = useRef(null);

  useEffect(() => {
    lireFournisseurLlm().then(setLlm).catch(() => setLlm(null));
    lireBriefing()
      .then((b) => setBriefing(b.points ?? []))
      .catch(() => setBriefing(null)); // sans briefing, la discussion reste utilisable
  }, []);

  useEffect(() => {
    finRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, chargement]);

  async function envoyer(e, texte) {
    e?.preventDefault();
    const question = (texte ?? saisie).trim();
    if (!question || chargement) return;

    const historique = messages
      .filter((m) => m.role === "user" || (m.role === "assistant" && m.mode))
      .slice(-MEMOIRE_MESSAGES)
      .map((m) => ({ role: m.role, contenu: m.contenu }));
    setMessages((m) => [...m, { role: "user", contenu: question }]);
    setSaisie("");
    setChargement(true);
    try {
      const reponse = await poserQuestion(question, historique);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          contenu: reponse.reponse,
          mode: reponse.mode,
          fournisseur: reponse.fournisseur,
          modele: reponse.modele,
          requetesSql: reponse.requetes_sql?.length ? reponse.requetes_sql : reponse.requete_sql ? [reponse.requete_sql] : [],
          lignesBase: reponse.lignes_base,
          outils: reponse.outils_utilises ?? [],
        },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", contenu: `⚠️ ${err.message}` }]);
    } finally {
      setChargement(false);
    }
  }

  return (
    <div className="flex h-[calc(100svh-4.5rem)] flex-col gap-3">
      {/* En-tête compact : la place va à la discussion. */}
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-light text-brand">
            <IconChat width={18} height={18} />
          </span>
          <div>
            <h1 className="font-display text-lg font-bold text-ink">Assistant Copilote</h1>
            <p className="text-xs text-muted">
              Lit vos données, fait les calculs (prévisions, simulations, commandes) et vous conseille.
            </p>
          </div>
        </div>
        {llm && (
          <p className="text-[11px] text-muted" data-testid="fournisseur-llm">
            {llm.configure ? (
              <>
                LLM : <strong className="text-ink">{llm.fournisseur}</strong> · {llm.modele}
                {llm.source && <span className="opacity-70"> (clé : {llm.source})</span>}
              </>
            ) : (
              "Aucune clé API LLM : synthèse factuelle locale."
            )}
            {llm.avertissements?.map((a) => (
              <span key={a} className="block text-amber-700">⚠️ {a}</span>
            ))}
          </p>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <div className="flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          <Briefing points={briefing} onQuestion={(q) => envoyer(null, q)} desactive={chargement} />

          {messages.length === 0 && (
            <div className="flex flex-col items-center py-6 text-center text-muted">
              <p className="mb-3 text-sm">Posez une question sur vos ventes, stocks, trésorerie ou clients. Par exemple :</p>
              <div className="flex max-w-2xl flex-wrap justify-center gap-2" data-testid="exemples-questions">
                {EXEMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => envoyer(null, ex)}
                    className="rounded-full border border-line bg-paper px-3 py-1.5 text-xs text-ink transition-colors hover:border-brand hover:text-brand"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "max-w-[75%] whitespace-pre-wrap bg-brand text-white"
                    : "w-full max-w-4xl border border-line bg-paper text-ink"
                }`}
              >
                {m.role === "user" ? m.contenu : <Markdown texte={m.contenu} />}
                {m.role === "assistant" && m.outils?.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2 text-[11px] text-muted" data-testid="outils-utilises">
                    <p>Calculs effectués :</p>
                    <ul className="mt-0.5 space-y-0.5">
                      {m.outils.map((o, j) => (
                        <li key={j} className={o.statut === "erreur" ? "text-danger" : undefined}>
                          {o.statut === "erreur" ? "✗" : "✓"} {o.libelle}
                          {decrireParametres(o.parametres) && ` (${decrireParametres(o.parametres)})`}
                          {o.statut === "erreur" && " — calcul impossible"}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.requetesSql?.length > 0 && (
                  <details className="mt-1.5 text-[11px] text-muted" data-testid="requete-sql">
                    <summary className="cursor-pointer select-none hover:text-ink">
                      Données lues dans la base ({m.lignesBase} ligne{m.lignesBase > 1 ? "s" : ""}
                      {m.requetesSql.length > 1 ? `, ${m.requetesSql.length} requêtes` : ""}) — voir{" "}
                      {m.requetesSql.length > 1 ? "les requêtes" : "la requête"}
                    </summary>
                    {m.requetesSql.map((sql, j) => (
                      <pre
                        key={j}
                        className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-surface p-2 font-mono text-[11px] text-ink"
                      >
                        {sql}
                      </pre>
                    ))}
                  </details>
                )}
                {m.role === "assistant" && m.mode && (
                  <p className="mt-2 text-[11px] text-muted" data-testid="origine-reponse">
                    {ORIGINE[m.mode]}
                    {m.fournisseur ? ` · ${m.fournisseur}${m.modele ? ` (${m.modele})` : ""}` : ""}
                  </p>
                )}
              </div>
            </div>
          ))}

          {chargement && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl border border-line bg-paper px-4 py-3 text-sm text-muted" role="status">
                <Spinner size={14} className="text-brand" />
                Le copilote analyse vos données et fait les calculs…
              </div>
            </div>
          )}
          <div ref={finRef} />
        </div>

        <form onSubmit={envoyer} className="flex items-center gap-2 border-t border-line p-3">
          <input
            value={saisie}
            onChange={(e) => setSaisie(e.target.value)}
            placeholder="Posez votre question…"
            className="flex-1 rounded-xl border border-line px-4 py-3 text-sm outline-none focus:border-brand"
          />
          <button
            type="submit"
            disabled={!saisie.trim() || chargement}
            aria-label="Envoyer"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand text-white transition-colors hover:bg-brand-dark disabled:opacity-40"
          >
            <IconSend />
          </button>
        </form>
      </div>
    </div>
  );
}
