#!/bin/sh
set -eu

# Placé dans /docker-entrypoint.d/ : exécuté automatiquement par
# l'entrypoint natif de l'image nginx:alpine avant le démarrage de Nginx —
# ne doit PAS `exec` quoi que ce soit ni bloquer, juste faire son travail
# et rendre la main (l'image de base enchaîne avec les autres scripts du
# dossier puis lance `nginx -g "daemon off;"`).
#
# Substitue ${API_BASE_URL} dans config.js avec la valeur RÉELLE de la
# variable d'environnement au démarrage du conteneur (pas au build de
# l'image) — permet de changer l'URL sans reconstruire l'image. Si la
# variable n'est pas définie (cas par défaut recommandé, cf. nginx.conf qui
# fait déjà tout le travail via le reverse-proxy), le fichier substitué
# contient une chaîne vide : src/api/client.js retombe alors sur le chemin
# relatif "/api/v1" par défaut, ce qui est le comportement voulu.
envsubst '${API_BASE_URL}' < /usr/share/nginx/html/config.js > /tmp/config.js
mv /tmp/config.js /usr/share/nginx/html/config.js
