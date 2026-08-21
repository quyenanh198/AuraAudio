import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  optimizeDeps: {
    // jspdf/svg2pdf.js (lib/exportPdf.ts) are reached ONLY via a dynamic
    // `import()` from Sidebar.svelte's PDF-export click handler (kept
    // dynamic on purpose — see exportPdf.ts's own comment on bundle
    // size), so Vite's dev-server dependency scanner doesn't always
    // discover them at cold start. Left to lazy discovery, the FIRST
    // click that reaches that import triggers a disruptive
    // "optimized dependencies changed, reloading" full-page reload
    // mid-interaction (observed directly: it broke
    // e2e/edit-journey.spec.ts's PDF-export step, which never sees its
    // own click land because the page reloads under it). Listing them
    // here pre-bundles both at server startup instead, so no such reload
    // happens on first use.
    include: ['jspdf', 'svg2pdf.js'],
  },
})
