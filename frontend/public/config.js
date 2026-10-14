// Généré/substitué par docker/entrypoint.sh au démarrage du conteneur
// (envsubst remplace ${API_BASE_URL} par la vraie valeur, si la variable
// d'environnement est définie). PAR DÉFAUT, ce n'est pas nécessaire : le
// client API (src/api/client.js) utilise un chemin RELATIF ("/api/v1") que
// Nginx (prod) et le proxy du serveur de dev Vite redirigent tous deux vers
// le service API — le navigateur n'a besoin de connaître qu'UNE origine.
// Ce mécanisme reste utile uniquement pour un déploiement avancé où le
// frontend et l'API vivent sur des origines réellement différentes (pas de
// reverse-proxy commun) — dans ce cas seulement, définir API_BASE_URL dans
// l'environnement du conteneur `dashboard`.
window.__APP_CONFIG__ = {
  API_BASE_URL: "${API_BASE_URL}",
};
