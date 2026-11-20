import react from '@vitejs/plugin-react';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: '/console/',
  plugins: [react()],
  root,
  build: {
    outDir: resolve(root, 'dist'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
  },
});
