/* global clients, self */
// OmniSync push notification service worker
// Handles incoming push events and notification click actions.

self.addEventListener('push', (event) => {
  let data = { title: 'OmniSync', body: '', event_type: '' };
  try {
    if (event.data) {
      data = { ...data, ...event.data.json() };
    }
  } catch {
    data.body = event.data?.text() ?? 'New notification';
  }

  event.waitUntil(
    self.registration.showNotification(data.title || 'OmniSync', {
      body: data.body,
      icon: '/favicon.ico',
      tag:  data.event_type || 'omnisync',
      data: { url: '/' },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && 'focus' in client) {
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});

// The push service replaced or expired the subscription: subscribe again
// with the same key and tell the backend (the /api proxy adds the token),
// so notifications keep arriving without a visit to the settings page.
function toBase64url (buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function resubscribe (event) {
  const old = event.oldSubscription;
  const next = event.newSubscription ||
    (old ? await self.registration.pushManager.subscribe(old.options) : null);
  if (old && (!next || old.endpoint !== next.endpoint)) {
    await fetch('/api/notifications/push-subscription', {
      method:  'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ endpoint: old.endpoint }),
    }).catch(() => {});
  }
  if (!next) return;
  await fetch('/api/notifications/push-subscription', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      endpoint: next.endpoint,
      keys:     { p256dh: toBase64url(next.getKey('p256dh')), auth: toBase64url(next.getKey('auth')) },
    }),
  });
}

self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(resubscribe(event).catch(() => {}));
});
