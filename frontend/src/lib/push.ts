import { api } from '@/lib/api';

// Web Push in this browser: the service worker, the browser's push
// subscription, and the copy of it the backend stores.
//
// The subscription is state to reconcile, not a side effect of the toggle:
// the settings page calls syncPushSubscription() whenever the channel is on,
// so a browser that already allowed notifications is (re)registered with
// the backend without another click, and the backend's copy stays current.

export type PushResult =
  | { ok: true }
  | { ok: false; reason: 'unsupported' | 'denied' | 'failed'; message?: string };

/** What this browser can do: 'subscribed' means a push subscription exists. */
export type BrowserPushState = 'unsupported' | 'denied' | 'prompt' | 'unsubscribed' | 'subscribed';

const SW_URL = '/sw.js';

export function pushSupported (): boolean {
  return typeof window !== 'undefined' &&
    typeof Notification !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window;
}

function urlBase64ToUint8Array (base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const output = new Uint8Array(raw.length) as Uint8Array<ArrayBuffer>;
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

function arrayBufferToBase64url (buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function registration (): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register(SW_URL);
  return navigator.serviceWorker.ready;
}

/** Store a browser subscription on the backend (idempotent: known endpoints are updated). */
async function saveSubscription (subscription: PushSubscription): Promise<boolean> {
  const p256dh = subscription.getKey('p256dh');
  const auth = subscription.getKey('auth');
  if (!p256dh || !auth) return false;
  await api.subscribePush({
    endpoint: subscription.endpoint,
    keys:     { p256dh: arrayBufferToBase64url(p256dh), auth: arrayBufferToBase64url(auth) },
  });
  return true;
}

export async function browserPushState (): Promise<BrowserPushState> {
  if (!pushSupported()) return 'unsupported';
  if (Notification.permission === 'denied') return 'denied';
  if (Notification.permission !== 'granted') return 'prompt';
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_URL);
    const subscription = reg ? await reg.pushManager.getSubscription() : null;
    return subscription ? 'subscribed' : 'unsubscribed';
  } catch {
    return 'unsubscribed';
  }
}

/** Ask for permission (needs a click), subscribe this browser and register it with the backend. */
export async function subscribeToPush (): Promise<PushResult> {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  try {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return { ok: false, reason: 'denied' };

    const reg = await registration();
    let subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
      const { public_key: publicKey } = await api.getVapidPublicKey();
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly:      true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    if (!(await saveSubscription(subscription))) return { ok: false, reason: 'failed' };
    return { ok: true };
  } catch (err) {
    console.error('Failed to subscribe to push notifications:', err);
    return { ok: false, reason: 'failed', message: err instanceof Error ? err.message : undefined };
  }
}

/** Remove this browser's subscription from the backend and the browser. */
export async function unsubscribeFromPush (): Promise<void> {
  if (!pushSupported()) return;
  const reg = await navigator.serviceWorker.getRegistration(SW_URL);
  const subscription = reg ? await reg.pushManager.getSubscription() : null;
  if (!subscription) return;
  try {
    await api.unsubscribePush(subscription.endpoint);
  } catch (err) {
    // 404: the backend had already dropped it (e.g. after a 410 from the push service).
    console.warn('Could not remove the push subscription on the server:', err);
  }
  await subscription.unsubscribe();
}

/**
 * Reconcile while the channel is on: re-register an existing subscription
 * with the backend, or subscribe when the permission is already granted
 * (no prompt). Never asks for permission; returns the resulting state.
 */
export async function syncPushSubscription (): Promise<BrowserPushState> {
  const state = await browserPushState();
  if (state !== 'subscribed' && state !== 'unsubscribed') return state;
  try {
    const reg = await registration();
    let subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
      const { public_key: publicKey } = await api.getVapidPublicKey();
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly:      true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    return (await saveSubscription(subscription)) ? 'subscribed' : 'unsubscribed';
  } catch (err) {
    console.warn('Could not refresh the push subscription:', err);
    return state;
  }
}
