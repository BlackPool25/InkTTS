import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'InkTTS Read-Along',
    description: 'Novel read-along narrated by your LAN TTS stack.',
    permissions: ['storage', 'scripting', 'activeTab', 'tabs', 'offscreen'],
    host_permissions: ['http://*/*', 'https://*/*'],
    icons: {
      16: 'icons/icon-16.png',
      48: 'icons/icon-48.png',
      128: 'icons/icon-128.png',
    },
    action: {
      default_title: 'InkTTS',
      default_icon: {
        16: 'icons/icon-16.png',
        48: 'icons/icon-48.png',
        128: 'icons/icon-128.png',
      },
    },
  },
});
