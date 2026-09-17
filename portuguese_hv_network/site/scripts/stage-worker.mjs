import { copyFileSync } from 'node:fs';
// Keep Wrangler's module discovery inside the built server directory.
copyFileSync('scripts/worker-entry.js', 'dist/server/worker.js');
copyFileSync('download-manifest.js', 'dist/server/download-manifest.js');
