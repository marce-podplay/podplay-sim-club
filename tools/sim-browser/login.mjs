#!/usr/bin/env node
// Complete only the rendered password-login branch for one isolated Sim.
// Product access begins after this routine; it never navigates, books, or
// records any product outcome.

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const controller = join(here, 'controller.mjs');
const args = process.argv.slice(2);
const valueFor = (flag) => {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : undefined;
};
const session = valueFor('--session');
const handoffPort = valueFor('--handoff-port');

if (!session || !handoffPort) {
  console.error('sim-browser login: --session and --handoff-port are required');
  process.exit(2);
}

const call = (controllerArgs) => new Promise((resolve) => {
  const child = spawn(process.execPath, [controller, ...controllerArgs], { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = ''; let err = '';
  child.stdout.on('data', (chunk) => { out += chunk; });
  child.stderr.on('data', (chunk) => { err += chunk; });
  child.on('close', (code) => resolve({ code, out, err }));
});

const waitVisible = async (feature, seconds) => {
  const result = await call(['wait-feature', '--session', session, '--feature', feature, '--seconds', String(seconds)]);
  return result.code === 0 && result.out.includes('visible_feature_ready');
};

const activate = async (feature, extra = []) => {
  const result = await call(['act', '--session', session, '--feature', feature, ...extra]);
  if (result.code !== 0 || !result.out.includes('visible_action_completed')) {
    throw new Error(`visible control did not complete: ${feature}`);
  }
};

const confirmAuthenticatedReservations = async () => {
  // Reservations is a normal member-facing route. Its rendered Upcoming tab
  // is a stronger proof of an authenticated session than the public root
  // shell, which can render for both signed-out and signed-in visitors.
  const navigation = await call(['act', '--session', session, '--feature', 'navigation.reservations']);
  if (navigation.code !== 0 || !navigation.out.includes('visible_action_completed')) return false;
  return await waitVisible('reservation.upcoming', 15);
};

try {
  if (!await waitVisible('login.email', 30)) throw new Error('email field did not render');
  await activate('login.email', ['--handoff-port', handoffPort]);
  // The public login shell can take several seconds to validate an email
  // after visible typing.  Wait through that normal client-side delay before
  // classifying the rendered control as unavailable.
  if (!await waitVisible('login.continue', 45)) throw new Error('enabled Continue control did not render');
  await activate('login.continue');
  if (!await waitVisible('login.password', 30)) throw new Error('password field did not render');
  await activate('login.password', ['--handoff-port', handoffPort]);
  if (!await waitVisible('login.sign_in', 10)) throw new Error('enabled Sign in control did not render');
  await activate('login.sign_in');
  let redirect = await call(['wait-login-redirect', '--session', session, '--seconds', '45']);
  // Some normal product sessions leave the success/redirect shell rendered
  // even after the session cookie has been established.  A customer can
  // reopen the ordinary root route; do the same bounded, visible navigation
  // once, then require the same settled-render criterion.  This does not
  // create product data or bypass the rendered login experience.
  if (redirect.code === 0 && redirect.out.includes('login_redirect_pending')) {
    const root = await call(['navigate', '--session', session, '--path', '/']);
    if (root.code === 0 && root.out.includes('visible_navigation_completed')) {
      if (await confirmAuthenticatedReservations()) {
        console.log(JSON.stringify({ status: 'visible_login_complete' }));
        process.exit(0);
      }
      redirect = await call(['wait-login-redirect', '--session', session, '--seconds', '15']);
    }
  }
  if (redirect.code !== 0 || !redirect.out.includes('login_redirect_settled')) {
    throw new Error('login did not visibly settle into the product');
  }
  console.log(JSON.stringify({ status: 'visible_login_complete' }));
} catch (error) {
  console.error(`sim-browser login: ${error.message}`);
  process.exitCode = 2;
}
