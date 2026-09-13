import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { poserQuestion } from "../api/client";
import { PageHeader } from "../components/ui/PageHeader";
import { Spinner } from "../components/ui/Spinner";
import { IconChat, IconSend } from "../components/icons";

export function Copilote() {
  const [messages, setMessages] = useState([]);
  const [saisie, setSaisie] = useState("");
  const [chargement, setChargement] = useState(false);
  const finRef = useRef(null);

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
      setMessages((m) => [...m, { role: "assistant", contenu: reponse.reponse }]);
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
        subtitle="Répond à partir des indicateurs réels des deux modules, en appelant directement les APIs de prévision, trésorerie, stocks et segmentation."
      />

      <div className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <div className="flex-1 space-y-4 overflow-y-auto p-5 sm:p-6">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <span className="focus-ring mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-light text-brand">
                <IconChat width={22} height={22} />
              </span>
              <p className="text-sm">Posez une question sur vos ventes, stocks ou clients.</p>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "whitespace-pre-wrap bg-brand text-white"
                    : "border border-line bg-paper text-ink [&_p]:mb-2 [&_p:last-child]:mb-0 [&_strong]:font-semibold [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-line [&_th]:bg-brand-light [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1"
                }`}
              >
                {m.role === "assistant" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.contenu}</ReactMarkdown>
                ) : (
                  m.contenu
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
