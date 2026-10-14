import { useEffect, useRef, useState } from "react";
import { lireFournisseurLlm, poserQuestion } from "../api/client";
import { PageHeader } from "../components/ui/PageHeader";
import { Spinner } from "../components/ui/Spinner";
import { IconChat, IconSend } from "../components/icons";

// Origine de chaque réponse (champ `mode` de POST /copilot/chat) : l'utilisateur
// doit savoir s'il lit un texte rédigé par un LLM ou la synthèse de secours.
const ORIGINE = {
  llm: "Réponse rédigée par le LLM",
  local: "Synthèse factuelle locale — aucun LLM configuré",
  secours: "LLM indisponible — synthèse factuelle de secours",
};

export function Copilote() {
  const [messages, setMessages] = useState([]);
  const [saisie, setSaisie] = useState("");
  const [chargement, setChargement] = useState(false);
  const [llm, setLlm] = useState(null);
  const finRef = useRef(null);

  useEffect(() => {
    lireFournisseurLlm().then(setLlm).catch(() => setLlm(null));
  }, []);

  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chargement]);

  async function envoyer(e) {
    e.preventDefault();
    const question = saisie.trim();
    if (!question || chargement) return;

    setMessages((m) => [...m, { role: "user", contenu: question }]);
    setSaisie("");
    setChargement(true);
    try {
      const reponse = await poserQuestion(question);
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
        },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", contenu: `⚠️ ${err.message}` }]);
    } finally {
      setChargement(false);
    }
  }

  return (
    <div className="flex h-[calc(100svh-8rem)] flex-col">
      <PageHeader
        icon={IconChat}
        eyebrow="Module mutualisé"
        title="Assistant Copilote"
        subtitle="Répond à partir des indicateurs réels des deux modules (KPIs, alertes, segments, prévisions). Avec un LLM configuré, il consulte aussi toutes les données de l'application : la base en lecture seule (jusqu'à 3 requêtes SQL vérifiées, visibles sous la réponse), les prévisions des 3 modèles et l'historique des segmentations ; sinon, une synthèse factuelle est affichée."
      />

      {llm && (
        <div className="mb-3 rounded-xl border border-line bg-surface px-4 py-2 text-xs text-muted" data-testid="fournisseur-llm">
          {llm.configure ? (
            <span>
              LLM utilisé : <strong className="text-ink">{llm.fournisseur}</strong> · {llm.modele}{" "}
              <span className="opacity-70">(clé lue dans {llm.source})</span>
            </span>
          ) : (
            <span>Aucune clé API LLM dans le .env : le copilote répond par synthèse factuelle locale.</span>
          )}
          {llm.avertissements?.map((a) => (
            <p key={a} className="mt-1 text-amber-700">⚠️ {a}</p>
          ))}
        </div>
      )}

      <div className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <div className="flex-1 space-y-4 overflow-y-auto p-5 sm:p-6">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <span className="focus-ring mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-light text-brand">
                <IconChat width={22} height={22} />
              </span>
              <p className="text-sm">Posez une question sur vos ventes, stocks, trésorerie ou clients — par exemple « CA par pays » ou « produits jamais vendus ».</p>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "bg-brand text-white"
                    : "border border-line bg-paper text-ink"
                }`}
              >
                {m.contenu}
                {m.role === "assistant" && m.mode && (
                  <p className="mt-2 border-t border-line pt-1.5 text-[11px] text-muted" data-testid="origine-reponse">
                    {ORIGINE[m.mode]}
                    {m.fournisseur ? ` · ${m.fournisseur}${m.modele ? ` (${m.modele})` : ""}` : ""}
                  </p>
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
              </div>
            </div>
          ))}

          {chargement && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl border border-line bg-paper px-4 py-3 text-sm text-muted">
                <Spinner size={14} className="text-brand" />
                Analyse en cours…
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
            className="flex-1 rounded-xl border border-line px-4 py-2.5 text-sm outline-none focus:border-brand"
          />
          <button
            type="submit"
            disabled={!saisie.trim() || chargement}
            aria-label="Envoyer"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand text-white transition-colors hover:bg-brand-dark disabled:opacity-40"
          >
            <IconSend />
          </button>
        </form>
      </div>
    </div>
  );
}
