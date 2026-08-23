import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite 4 is pinned because the available Node runtime is v16.
export default defineConfig({
	plugins: [react()],
	server: {
		port: 5173,
	},
});
