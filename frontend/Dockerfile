# syntax=docker/dockerfile:1

# ── Étape 1 : build du bundle statique ───────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

# Copié seul en premier : un changement de code applicatif (le cas le plus
# fréquent) n'invalide jamais la couche `npm ci` ci-dessous, servie depuis
# le cache Docker tant que package*.json n'a pas changé.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY . .
RUN npm run build

# ── Étape 2 : image finale — Nginx sert le build + reverse-proxy /api ───
FROM nginx:1.27-alpine
RUN apk add --no-cache gettext  # envsubst, pour l'entrypoint de config runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/30-copilote-config.sh
RUN chmod +x /docker-entrypoint.d/30-copilote-config.sh

EXPOSE 8501

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://localhost:8501/ >/dev/null || exit 1
