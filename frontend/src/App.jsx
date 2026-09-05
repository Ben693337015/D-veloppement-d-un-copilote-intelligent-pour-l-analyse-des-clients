import { Suspense, lazy, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { LoadingBlock } from "./components/ui/Spinner";
import { Accueil } from "./pages/Accueil";
import { Connexion } from "./pages/Connexion";
import { Donnees } from "./pages/Donnees";
import { IntrouvablePage } from "./pages/IntrouvablePage";
import { getToken, onSessionExpiree } from "./api/client";

// Découpage par route pour les pages qui embarquent Recharts (lourd) —
// gardent le chargement initial rapide ("fluide", cf. demande explicite) ;
// seule la première visite d'une page de graphiques paie son propre coût.
const Segmentation = lazy(() => import("./pages/Segmentation").then((m) => ({ default: m.Segmentation })));
const VentesStocks = lazy(() => import("./pages/VentesStocks").then((m) => ({ default: m.VentesStocks })));
const Copilote = lazy(() => import("./pages/Copilote").then((m) => ({ default: m.Copilote })));
const Rapport = lazy(() => import("./pages/Rapport").then((m) => ({ default: m.Rapport })));

function PageDifferee({ children }) {
  return <Suspense fallback={<LoadingBlock label="Chargement de la page…" />}>{children}</Suspense>;
}

// Garde d'authentification (Phase 4) : redirige vers /connexion si aucun
// jeton n'est présent, en mémorisant la page visée (`state.depuis`) pour y
// revenir automatiquement une fois connecté — plutôt que de renvoyer
// systématiquement vers l'accueil après connexion.
function RouteProtegee({ estConnecte, children }) {
  const location = useLocation();
  if (!estConnecte) {
    return <Navigate to="/connexion" state={{ depuis: location.pathname }} replace />;
  }
  return children;
}

export function App() {
  const [estConnecte, setEstConnecte] = useState(() => Boolean(getToken()));

  // Un appel API renvoyant 401 en cours de session (jeton expiré, révoqué,
  // ou compte désactivé entre-temps) déclenche cet événement — cf.
  // api/client.js. Sans ça, l'utilisateur resterait sur une page qui
  // continue d'échouer silencieusement à chaque action.
  useEffect(() => onSessionExpiree(() => setEstConnecte(false)), []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/connexion" element={<Connexion onConnecte={() => setEstConnecte(true)} />} />
        <Route
          element={
            <RouteProtegee estConnecte={estConnecte}>
              <AppShell onDeconnecte={() => setEstConnecte(false)} />
            </RouteProtegee>
          }
        >
          <Route index element={<Accueil />} />
          <Route path="donnees" element={<Donnees />} />
          <Route path="segmentation" element={<PageDifferee><Segmentation /></PageDifferee>} />
          <Route path="ventes" element={<PageDifferee><VentesStocks /></PageDifferee>} />
          <Route path="copilote" element={<PageDifferee><Copilote /></PageDifferee>} />
          <Route path="rapport" element={<PageDifferee><Rapport /></PageDifferee>} />
          <Route path="*" element={<IntrouvablePage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
