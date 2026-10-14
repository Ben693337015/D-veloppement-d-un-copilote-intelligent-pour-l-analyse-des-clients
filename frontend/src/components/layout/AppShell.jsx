import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function AppShell({ onDeconnecte }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { pathname } = useLocation();

  // React Router (BrowserRouter) ne remet PAS le scroll en haut lors d'un
  // changement de page : si l'utilisateur était scrollé en bas d'une page,
  // la page suivante s'affiche à la même position de scroll — ce qui donne
  // l'impression d'un grand vide blanc en haut tant qu'on n'a pas remonté
  // manuellement. On force donc le retour en haut à chaque navigation.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <div className="min-h-svh bg-paper">
      <Sidebar open={sidebarOpen} onNavigate={() => setSidebarOpen(false)} onDeconnecte={onDeconnecte} />

      {sidebarOpen && (
        <button
          type="button"
          aria-label="Fermer le menu"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-ink/40 lg:hidden"
        />
      )}

      <div className="lg:pl-72">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-surface/90 px-4 py-3 backdrop-blur lg:hidden">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            aria-label="Ouvrir le menu"
            className="rounded-lg border border-line p-2 text-ink"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="font-display text-sm font-extrabold">Copilote IA PME</span>
        </header>

        <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
