// Starts the standalone server that the web UI image runs
// (.next/standalone/server.js) for the browser smoke tests: `next start`
// does not support `output: 'standalone'`. The build output lacks the
// static assets and public/, which the image copies in; this does the same.
import { cpSync, existsSync } from 'node:fs';
import { spawn } from 'node:child_process';

const port = process.argv[2];
if (!port) throw new Error('usage: node e2e/serve.mjs <port>');
const standalone = '.next/standalone';
if (!existsSync(`${standalone}/server.js`)) throw new Error('run `pnpm build` first');
cpSync('.next/static', `${standalone}/.next/static`, { recursive: true });
if (existsSync('public')) cpSync('public', `${standalone}/public`, { recursive: true });

const server = spawn(process.execPath, ['server.js'], {
  cwd:   standalone,
  stdio: 'inherit',
  env:   { ...process.env, HOSTNAME: '127.0.0.1', PORT: port },
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.kill(signal));
server.on('exit', (code) => process.exit(code ?? 1));
