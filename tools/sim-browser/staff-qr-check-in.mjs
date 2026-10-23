#!/usr/bin/env node
/**
 * Ephemeral QR-to-staff-scanner bridge.
 *
 * It never calls a product API and never writes the QR value to evidence,
 * stdout, a message, or durable state.  The only product effect possible is
 * the one the rendered staff scanner itself performs after receiving normal
 * keyboard events.
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const controller = join(here, 'controller.mjs');
const decoder = join(here, 'qr-decode.swift');
const values = Object.fromEntries(process.argv.slice(2).filter((_, i) => i % 2 === 0).map((key, i) => [key.replace(/^--/, ''), process.argv.slice(2)[i * 2 + 1]]));

function run(command, args, { stdin } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', code => code === 0 ? resolve(stdout) : reject(new Error(`${command} failed (${code}): ${stderr.trim() || 'no diagnostic'}`)));
    child.stdin.end(stdin ?? '');
  });
}

if (!values['player-session'] || !values['staff-session']) {
  console.error('usage: staff-qr-check-in.mjs --player-session PATH --staff-session PATH');
  process.exit(2);
}

const work = await mkdtemp(join(tmpdir(), 'club-lab-qr-'));
const image = join(work, 'qr.png');
try {
  await run(process.execPath, [controller, 'capture-qr', '--session', values['player-session'], '--out', image]);
  const raw = (await run('swift', [decoder, image])).trim();
  // Matches the staff scanner's reviewed input contract. Do not loosen this
  // without a new observed scanner protocol.
  if (!/^userId=[A-Za-z0-9_-]{28}$/.test(raw)) throw new Error('decoded QR is not an accepted staff-scanner payload');
  await run(process.execPath, [controller, 'scan-code', '--session', values['staff-session']], { stdin: raw });
  console.log(JSON.stringify({ status: 'staff_qr_handoff_dispatched', outcome: 'rendered_staff_readback_required' }));
} finally {
  await rm(work, { recursive: true, force: true, maxRetries: 4, retryDelay: 100 });
}
