// Offscreen audio owner (AUDIO_PLAYBACK): SSE + prefetch + Web Audio
// chaining live here in Task 5. Created on demand, killed ~30s after
// silence — resume is always {hash, idx, offsetMs} + Range re-fetch.
console.debug('[inkcast] offscreen alive');
