# Frontend — Copilote IA PME

Application React (Vite + Tailwind + React Router + Recharts). La documentation complète —
architecture, variables d'environnement, exécution avec ou sans Docker, tests — vit dans le
[README principal du projet](../README.md), sections **§2 (Dépendances)**, **§3 (Architecture)**
et **§7 (Docker / sans Docker)**.

Démarrage rapide (API déjà lancée sur `localhost:8000`, cf. README principal) :

```bash
npm install
npm run dev
```

Ouvrir **http://localhost:5173**.

```bash
npm run build   # build de production (dist/)
npx vitest run  # suite de tests (jsdom, sans navigateur réel)
npx oxlint       # lint
```
