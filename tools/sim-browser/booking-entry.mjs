#!/usr/bin/env node
// Resolve the first visible branch of a Sim's booking journey.
// This helper deliberately stops before date, slot, pod, review, or submission.

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

if (!session) {
  console.error('sim-browser booking-entry: --session is required');
  process.exit(2);
}

const call = (controllerArgs) => new Promise((resolve) => {
  const child = spawn(process.execPath, [controller, ...controllerArgs], { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = ''; let err = '';
  child.stdout.on('data', (chunk) => { out += chunk; });
  child.stderr.on('data', (chunk) => { err += chunk; });
  child.on('close', (code) => resolve({ code, out, err }));
});

const visible = async (feature, seconds) => {
  const result = await call(['wait-feature', '--session', session, '--feature', feature, '--seconds', String(seconds)]);
  return result.code === 0 && result.out.includes('visible_feature_ready');
};

const activate = async (feature) => {
  const result = await call(['act', '--session', session, '--feature', feature]);
  if (result.code !== 0 || !result.out.includes('visible_action_completed')) {
    throw new Error(`visible control did not complete: ${feature}`);
  }
};

try {
  let branch = 'already_onboarded';
  // Login can finish before the region dialog renders. Give the visible
  // branch time to settle instead of guessing that onboarding is absent.
  if (await visible('onboarding.region.new_york_new_jersey', 20)) {
    branch = 'region_onboarding';
    await activate('onboarding.region.new_york_new_jersey');
    if (!await visible('onboarding.region.confirm', 10)) throw new Error('region confirmation did not render');
    await activate('onboarding.region.confirm');
  }
  if (!await visible('navigation.booking', 20)) throw new Error('Booking navigation did not render');
  await activate('navigation.booking');
  if (!await visible('booking.area.west_37th_manhattan', 30)) throw new Error('West 37th area control did not render');
  // Area and experience are durable parts of the named Sim's agreed Club
  // context.  Selecting them only reveals live availability; it does not
  // choose a volatile time, payment, or create a booking.
  await activate('booking.area.west_37th_manhattan');
  if (!await visible('booking.experience.open_pod', 20)) throw new Error('Open Pod experience did not render');
  await activate('booking.experience.open_pod');
  console.log(JSON.stringify({ status: 'booking_entry_ready', branch, nextVisibleControl: 'live_availability' }));
} catch (error) {
  console.error(`sim-browser booking-entry: ${error.message}`);
  process.exitCode = 2;
}
