import { useEffect, useState } from "react";
import { checkHealth } from "../api/client";

/** Vérifie la connexion à l'API au montage puis toutes les 20s — affiché
 * dans la barre latérale pour donner un signal clair plutôt que de laisser
 * chaque page échouer silencieusement si l'API n'est pas joignable. */
export function useApiHealth() {
  const [online, setOnline] = useState(null); // null = vérification en cours

  useEffect(() => {
    let annule = false;
    async function verifier() {
      const ok = await checkHealth();
      if (!annule) setOnline(ok);
    }
    verifier();
    const intervalle = setInterval(verifier, 20_000);
    return () => {
      annule = true;
      clearInterval(intervalle);
    };
  }, []);

  return online;
}
