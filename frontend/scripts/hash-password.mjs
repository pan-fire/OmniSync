#!/usr/bin/env node
// Make a password hash for OMNISYNC_UI_PASSWORD_HASH (the web UI's login).
//
//   docker compose exec frontend node scripts/hash-password.mjs
//   node frontend/scripts/hash-password.mjs              (with Node 20+)
//   printf '%s' "$PASSWORD" | node scripts/hash-password.mjs
//
// The password is read from the terminal without echo (asked twice), or
// from standard input when that is not a terminal; never from the command
// line, where it would show in the process list and the shell history.
// Only the hash is printed. Same format as src/lib/auth/password.ts:
// scrypt:<log2 N>:<r>:<p>:<salt>:<key>, base64url, no `$` (which compose
// would interpolate).

import { randomBytes, scrypt } from 'node:crypto';
import process from 'node:process';

const PARAMS = { logN: 15, r: 8, p: 3 };
const MIN_LENGTH = 12;

function hash (password) {
  const salt = randomBytes(16);
  const N = 2 ** PARAMS.logN;
  return new Promise((resolve, reject) => {
    scrypt(password.normalize('NFC'), salt, 32, { N, r: PARAMS.r, p: PARAMS.p, maxmem: 256 * N * PARAMS.r }, (error, key) => {
      if (error) reject(error);
      else resolve(['scrypt', PARAMS.logN, PARAMS.r, PARAMS.p, salt.toString('base64url'), key.toString('base64url')].join(':'));
    });
  });
}

/** One line from the terminal, not echoed. */
function askHidden (prompt) {
  return new Promise((resolve, reject) => {
    const { stdin, stderr } = process;
    stderr.write(prompt);
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    let value = '';
    const done = (error) => {
      stdin.setRawMode(false);
      stdin.pause();
      stdin.off('data', onData);
      stderr.write('\n');
      if (error) reject(error);
      else resolve(value);
    };
    const onData = (chunk) => {
      for (const char of chunk) {
        if (char === '\r' || char === '\n' || char === '\u0004') return done();
        if (char === '\u0003') return done(new Error('Cancelled.'));
        if (char === '\u007f' || char === '\b') value = [...value].slice(0, -1).join('');
        else if (char >= ' ') value += char;
      }
    };
    stdin.on('data', onData);
  });
}

async function readStdin () {
  let text = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) text += chunk;
  // One trailing newline (from echo or a file) is not part of the password.
  return text.replace(/\r?\n$/, '');
}

async function main () {
  let password;
  if (process.stdin.isTTY) {
    password = await askHidden('Password for the OmniSync web UI: ');
    const again = await askHidden('Repeat it: ');
    if (password !== again) throw new Error('The passwords do not match.');
  } else {
    password = await readStdin();
  }
  if ([...password].length < MIN_LENGTH) {
    throw new Error(`Use at least ${MIN_LENGTH} characters (a few random words work well).`);
  }
  if (password.length > 1024) throw new Error('Use at most 1024 characters.');
  process.stdout.write(`${await hash(password)}\n`);
  if (process.stdout.isTTY) {
    process.stderr.write('Put it in .env as OMNISYNC_UI_PASSWORD_HASH=<the line above> and restart the web UI.\n');
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
});
