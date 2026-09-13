import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import logo from "../assets/logo.jpg";
import { login, register } from "../api/client";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Alert } from "../components/ui/Alert";

export function Connexion({ onConnecte }) {
  const [mode, setMode] = useState("connexion"); // "connexion" | "inscription"
  const [email, setEmail] = useState("");
  const [motDePasse, setMotDePasse] = useState("");
  const [nom, setNom] = useState("");
  const [chargement, setChargement] = useState(false);
  const [erreur, setErreur] = useState(null);
  const navigate = useNavigate();
  const location = useLocation();

  async function soumettre(e) {
    e.preventDefault();
    setErreur(null);
    setChargement(true);
    try {
      if (mode === "inscription") {
        await register(email, motDePasse, nom || undefined);
      }
      await login(email, motDePasse);
      onConnecte();
      const destination = location.state?.depuis || "/";
      navigate(destination, { replace: true });
    } catch (err) {
      setErreur(err.message);
    } finally {
      setChargement(false);
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <img src={logo} alt="Copilote IA PME" className="mb-4 h-16 w-16 rounded-full object-cover" />
          <p className="font-display text-lg font-extrabold text-ink">Copilote IA PME</p>
          <p className="text-xs text-muted">Groupe 2 · Master IA Appliquée</p>
        </div>

        <Card>
          <div className="mb-5 flex rounded-xl bg-paper p-1 text-sm font-medium">
            <button
              type="button"
              onClick={() => setMode("connexion")}
              className={`flex-1 rounded-lg py-2 transition-colors ${
                mode === "connexion" ? "bg-surface text-ink shadow-sm" : "text-muted"
              }`}
            >
              Connexion
            </button>
            <button
              type="button"
              onClick={() => setMode("inscription")}
              className={`flex-1 rounded-lg py-2 transition-colors ${
                mode === "inscription" ? "bg-surface text-ink shadow-sm" : "text-muted"
              }`}
            >
              Créer un compte
            </button>
          </div>

          <form onSubmit={soumettre} className="space-y-4">
            {mode === "inscription" && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink">Nom (optionnel)</span>
                <input
                  type="text"
                  value={nom}
                  onChange={(e) => setNom(e.target.value)}
                  className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
                  placeholder="Paul"
                />
              </label>
            )}
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
                placeholder="vous@entreprise.cm"
                autoComplete="email"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink">Mot de passe</span>
              <input
                type="password"
                required
                minLength={mode === "inscription" ? 8 : undefined}
                value={motDePasse}
                onChange={(e) => setMotDePasse(e.target.value)}
                className="rounded-xl border border-line px-3.5 py-2.5 text-sm outline-none focus:border-brand"
                placeholder="••••••••"
                autoComplete={mode === "inscription" ? "new-password" : "current-password"}
              />
              {mode === "inscription" && (
                <span className="text-xs text-muted">8 caractères minimum.</span>
              )}
            </label>

            {erreur && <Alert tone="danger">{erreur}</Alert>}

            <Button type="submit" loading={chargement} className="w-full justify-center">
              {mode === "connexion" ? "Se connecter" : "Créer mon compte"}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
