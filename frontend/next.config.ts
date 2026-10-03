import type { NextConfig } from 'next';
import packageJson from './package.json';

// Everything the pages load comes from this server: scripts, styles, the
// self-hosted fonts and /api. Next's bootstrap and next-themes' theme script
// are inline, hence 'unsafe-inline' for scripts (a per-request nonce would
// need the proxy on every page); dev mode's React also needs eval.
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "worker-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'no-referrer' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=()' },
];

// /api requests are forwarded to the backend by src/proxy.ts, which reads
// BACKEND_URL at run time; a rewrite here would fix it at build time.
const nextConfig: NextConfig = {
  reactCompiler: true,
  // A self-contained server (.next/standalone/server.js) for the image.
  output:        'standalone',
  // The release version (kept equal to the repository's VERSION file by
  // scripts/release/prepare.sh), inlined into the client bundle at build time.
  env:           {
    NEXT_PUBLIC_OMNISYNC_VERSION: packageJson.version,
  },
  // The app shows no remote or user-supplied images, so the image optimizer
  // (/_next/image, a past source of RCE and DoS advisories) stays off.
  images: {
    unoptimized: true,
  },
  experimental: {
    // How long a proxied /api request may wait for the backend's answer
    // (Next's default is 30 s). Syncs, backups, restores and per-file
    // actions answer once they are under way (clients then poll the job);
    // the longest call that still waits is a check or diff, which the
    // backend bounds by OMNISYNC_CHECK_TIMEOUT (15 minutes by default).
    // Raise this with that timeout.
    proxyTimeout: 20 * 60 * 1000,
  },
  async headers () {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
