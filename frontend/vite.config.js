import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      // Miroir du reverse-proxy Nginx de production (cf. frontend/nginx.conf) :
      // le code applicatif appelle toujours un chemin relatif ("/api/v1"),
      // jamais une URL absolue vers un hôte Docker interne injoignable
      // depuis le navigateur. `npm run dev` suppose l'API sur localhost:8000
      // (cf. README pour lancer l'API sans Docker en parallèle).
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Sépare les grosses dépendances tierces dans leurs propres chunks
        // navigateur — mises en cache indépendamment du code applicatif,
        // qui change bien plus souvent (meilleure fluidité au chargement).
        manualChunks(id) {
          if (id.includes("node_modules/recharts") || id.includes("node_modules/d3-")) return "charts";
          if (id.includes("node_modules/react-router")) return "vendor";
          if (id.includes("node_modules/react") || id.includes("node_modules/scheduler")) return "vendor";
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
  },
})
