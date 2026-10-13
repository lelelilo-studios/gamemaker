'use strict';
// Lelelilo Gamemaker's service worker. It only shows the notifications the board sends when the Lelelilo finishes a
// prompt, and opens the board on that prompt when one is tapped. It caches nothing: the board is always fetched fresh.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let msg = {};
  try { msg = e.data ? e.data.json() : {}; } catch (err) { msg = { title: e.data ? e.data.text() : '' }; }
  e.waitUntil((async () => {
    // the board is open and in front: it shows the news itself (Neri holds the game up)
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const watching = wins.find(w => w.focused && w.visibilityState === 'visible');
    if (watching && msg.id != null) {
      watching.postMessage({ kind: 'finished', id: msg.id });
      return;
    }
    await self.registration.showNotification(msg.title || 'Lelelilo Gamemaker', {
      body: msg.body || '',
      tag: msg.tag || 'gamemaker',
      renotify: true,
      icon: 'icons/icon-192-v1.png',
      data: { url: msg.url || '/' },
    });
  })());
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const board = wins.find(w => new URL(w.url).origin === self.location.origin);
    if (board) {
      await board.focus();
      board.postMessage({ kind: 'open', url });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
