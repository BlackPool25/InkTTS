import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'InkCast Read-Along',
    description: 'Novel read-along narrated by your LAN TTS stack.',
    permissions: ['storage', 'scripting', 'activeTab', 'tabs', 'offscreen'],
    host_permissions: ['http://*/*', 'https://*/*'],
  },
});
