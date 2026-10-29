#!/usr/bin/env node
/**
 * Minimal, dependency-free CDP observer.  It is intentionally observation
 * only: no Runtime.evaluate, cookies, headers, bodies, or product API calls.
 */
import { spawn } from 'node:child_process';
import { mkdir, readFile, rm, writeFile, chmod, access, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.SIM_BROWSER_CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const usage = 'usage: controller.mjs start|navigate|open-private-invite|observe|snapshot|viewport|dom-snapshot|wait-feature|wait-login-redirect|capture-invite-link|capture-qr|scan-code|act|close [--actor label] [--origin https://…] [--session path] [--intent intent-ref] [--out path] [--headed] [--card-screen text] [--card-observed text] [--card-next text] [--card-outcome text]';
const args = Object.fromEntries(process.argv.slice(3).filter((_, i, a) => i % 2 === 0).map((key, i) => [key.replace(/^--/, ''), process.argv.slice(3)[i * 2 + 1]]));
const command = process.argv[2];
const headed = process.argv.slice(3).includes('--headed');
const fail = (message) => { console.error(`sim-browser: ${message}`); process.exitCode = 2; };

function assertOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' && url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') throw new Error('origin must be HTTPS or local');
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('origin must be an origin only');
  return url.origin;
}
function assertInitialPath(value) {
  if (!value) return '/';
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('://')) throw new Error('--path must be an origin-relative path');
  const url = new URL(value, 'https://example.invalid');
  if (url.origin !== 'https://example.invalid' || url.username || url.password || url.hash) throw new Error('--path is invalid');
  return `${url.pathname}${url.search}`;
}
function redact(value) {
  return String(value ?? '')
    // Invite links are a customer-access capability even when the app renders
    // them as bare host/path text (without an http scheme).  Keep structural
    // snapshots useful without retaining a reusable invite token.
    .replace(/(?:[A-Za-z0-9-]+\.)+a\.run\.app\/invites\/[A-Za-z0-9_-]+/g, '[invite_link]')
    .replace(/\/invites\/[A-Za-z0-9_-]+/g, '/invites/[token]')
    .replace(/https?:\/\/[^\s)]+/g, '[url]')
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[email]')
    .replace(/\b[A-Za-z0-9_-]{20,}\b/g, '[token]')
    .slice(0, 240);
}
function routeTemplate(urlValue) {
  try {
    const url = new URL(urlValue);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return 'non_http_resource';
    // Product routes can contain Firebase-style identifiers which are neither
    // UUIDs nor hex. Treat any long opaque path segment as an identifier so a
    // diagnostic keeps the route shape without retaining a customer ID.
    const pieces = url.pathname.split('/').filter(Boolean).map((part) =>
      /^[0-9a-f]{8,}$/i.test(part) || /^\d+$/.test(part) || /^[A-Za-z0-9_-]{16,}$/.test(part) || part.length > 48 ? ':id' : part);
    return `/${pieces.join('/') || ''}`;
  } catch { return 'unknown_route'; }
}
async function json(url) { const response = await fetch(url); if (!response.ok) throw new Error(`local CDP endpoint returned ${response.status}`); return response.json(); }
async function waitForPort(profile) {
  const marker = join(profile, 'DevToolsActivePort');
  for (let tries = 0; tries < 100; tries += 1) {
    try { return (await readFile(marker, 'utf8')).trim().split('\n')[0]; } catch { await sleep(100); }
  }
  throw new Error('Chrome did not publish a CDP port');
}
async function loadSession() { if (!args.session) throw new Error('--session is required'); return JSON.parse(await readFile(resolve(args.session), 'utf8')); }
async function saveSession(path, value) { await mkdir(resolve(path, '..'), { recursive: true }); await writeFile(path, JSON.stringify(value, null, 2), { mode: 0o600 }); await chmod(path, 0o600); }
async function target(session) {
  const pages = await json(`http://127.0.0.1:${session.port}/json/list`);
  const page = pages.find((row) => row.id === session.pageId && row.type === 'page')
    ?? pages.find((row) => row.type === 'page' && typeof row.url === 'string' && row.url.startsWith(session.origin));
  if (!page?.webSocketDebuggerUrl) throw new Error('no page for the isolated browser origin');
  return page;
}
async function followNewSameOriginTarget(session, priorPageId) {
  // Some product links intentionally open an ordinary same-origin management
  // page in a new tab. Continue with that visible tab, never a cross-origin
  // window, and persist only Chrome's non-sensitive target id.
  await sleep(250);
  const pages = await json(`http://127.0.0.1:${session.port}/json/list`);
  const next = pages.find((row) => row.type === 'page' && row.id !== priorPageId
    && typeof row.url === 'string' && row.url.startsWith(session.origin));
  if (!next?.id || !next.webSocketDebuggerUrl) return;
  session.pageId = next.id;
  await saveSession(resolve(args.session), session);
}
async function attach(session) {
  const page = await target(session); const socket = new WebSocket(page.webSocketDebuggerUrl); const callbacks = new Map(); let id = 0;
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  socket.onmessage = ({ data }) => { const message = JSON.parse(data); if (message.id && callbacks.has(message.id)) { callbacks.get(message.id)(message); callbacks.delete(message.id); } else if (listener) listener(message); };
  let listener = null;
  const send = (method, params = {}) => new Promise((ok, bad) => { const requestId = ++id; callbacks.set(requestId, (response) => response.error ? bad(new Error(response.error.message)) : ok(response.result)); socket.send(JSON.stringify({ id: requestId, method, params })); });
  return { socket, send, listen: (fn) => { listener = fn; } };
}
async function feature(name) {
  const map = JSON.parse(await readFile(new URL('./feature-map.json', import.meta.url), 'utf8'));
  const row = map.features?.[name];
  const validSelector = typeof row?.selector === 'string';
  const validAccessibleName = typeof row?.role === 'string' && typeof row.name === 'string';
  const validCoordinates = Number.isFinite(row?.coordinates?.x) && Number.isFinite(row?.coordinates?.y);
  const validClickTarget = row?.clickTarget === undefined || row.clickTarget === 'right_edge';
  if (!row || !['visible_click', 'visible_text_replace', 'secure_browser_handoff_fill'].includes(row.kind) || (!validSelector && !validAccessibleName && !validCoordinates) || !validClickTarget) throw new Error('feature is not allowlisted');
  if (row.kind === 'visible_text_replace' && (typeof row.value !== 'string' || !row.value)) throw new Error('text replacement must have an allowlisted value');
  return row;
}
async function nodeFor(cdp, mapped) {
  // Chrome may otherwise expose only the document shell to a fresh CDP
  // attachment, even though the page is visibly rendered.
  await cdp.send('DOM.enable');
  if (mapped.coordinates) return null;
  if (typeof mapped.selector !== 'string') {
    // The AX tree includes the backend DOM references needed for a real
    // visible interaction only after its domain has been explicitly enabled
    // on this CDP attachment.  Snapshot enables it independently, which had
    // made an observed time row look actionable while `act` could not yet
    // resolve it.
    await cdp.send('Accessibility.enable');
    await cdp.send('DOM.getDocument');
    const tree = await cdp.send('Accessibility.getFullAXTree');
    // Availability capacity is live product text (for example, "3 open Pods"
    // rather than "4 open Pods").  A mapped `namePrefix` keeps the action
    // constrained to one visible time row without treating a capacity change
    // as a different control.
    const nameMatches = (node) => typeof mapped.namePrefix === 'string'
      ? node.name?.value?.startsWith(mapped.namePrefix)
      : node.name?.value === mapped.name;
    const matches = (tree.nodes ?? []).filter((node) => node.role?.value === mapped.role && nameMatches(node) && Number.isInteger(node.backendDOMNodeId));
    if (!matches.length) throw new Error('allowlisted visible control is not present');
    if (matches.every((match) => match.properties?.some((property) => property.name === 'disabled' && property.value?.value === true))) {
      throw new Error('allowlisted visible control is disabled');
    }
    // Responsive pages can expose a hidden duplicate in the accessibility
    // tree. Select only a matching enabled control with rendered geometry.
    for (const match of matches) {
      if (match.properties?.some((property) => property.name === 'disabled' && property.value?.value === true)) continue;
      const pushed = await cdp.send('DOM.pushNodesByBackendIdsToFrontend', { backendNodeIds: [match.backendDOMNodeId] });
      const candidate = pushed.nodeIds?.[0];
      if (!candidate) continue;
      try {
        // A real homepage call-to-action may be below the viewport. Bringing
        // this exact allowlisted control into view is a visible browser
        // scroll, not page-script execution.
        await cdp.send('DOM.scrollIntoViewIfNeeded', { nodeId: candidate });
        await cdp.send('DOM.getBoxModel', { nodeId: candidate });
        return candidate;
      } catch {}
    }
    throw new Error('allowlisted visible control has no rendered box');
  }
  const document = await cdp.send('DOM.getDocument');
  const selected = await cdp.send('DOM.querySelector', { nodeId: document.root.nodeId, selector: mapped.selector });
  if (!selected.nodeId) throw new Error('allowlisted visible control is not present');
  return selected.nodeId;
}
async function act() {
  const session = await loadSession();
  if (!args.feature) throw new Error('--feature is required');
  const mapped = await feature(args.feature); const cdp = await attach(session);
  try {
    const priorPage = await target(session);
    await cdp.send('Page.bringToFront');
    const nodeId = await nodeFor(cdp, mapped);
    if (mapped.kind === 'visible_click') {
      const model = nodeId ? await cdp.send('DOM.getBoxModel', { nodeId }) : null; const quad = model?.model.content;
      const centerX = quad ? (quad[0] + quad[2] + quad[4] + quad[6]) / 4 : mapped.coordinates.x, y = quad ? (quad[1] + quad[3] + quad[5] + quad[7]) / 4 : mapped.coordinates.y;
      // A time row is exposed as one accessible button while its visible
      // selection affordance sits at the right edge.  The map supplies the
      // measured toggle-center inset, so this remains a visible pointer
      // action rather than a selector-only or script-driven state change.
      const x = quad && mapped.clickTarget === 'right_edge'
        ? Math.max(quad[0], quad[2], quad[4], quad[6]) - (Number.isFinite(mapped.rightEdgeInset) ? mapped.rightEdgeInset : 50)
        : centerX;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0, pointerType: 'mouse' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1, pointerType: 'mouse' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1, pointerType: 'mouse' });
      // Some accessible radio controls only commit after a standard keyboard
      // activation.  This remains a visible, allowlisted UI action.
      if (mapped.key === 'Space') {
        if (nodeId) await cdp.send('DOM.focus', { nodeId });
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 });
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 });
      }
      await followNewSameOriginTarget(session, priorPage.id);
    } else if (mapped.kind === 'visible_text_replace') {
      const { x, y } = mapped.coordinates;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0, pointerType: 'mouse' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1, pointerType: 'mouse' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1, pointerType: 'mouse' });
      // Chrome runs on macOS in this environment, so use Command+A (not
      // Control+A) to replace the visible native time value as a person would.
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 4, windowsVirtualKeyCode: 65 });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 4, windowsVirtualKeyCode: 65 });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
      for (const character of mapped.value) {
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: character, text: character, unmodifiedText: character });
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: character });
      }
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
    } else {
      const port = Number(args['handoff-port']);
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('secure fill requires a valid --handoff-port');
      // Do not make the page fetch a loopback address: Chrome's Private
      // Network Access prompt can hold that request indefinitely. The local
      // controller uses the existing exact-origin handoff, then sends real
      // CDP keyboard input to the focused visible control.
      const abort = new AbortController(); const timer = setTimeout(() => abort.abort(), 5_000);
      let value;
      try {
        const response = await fetch(`http://127.0.0.1:${port}/v1/credential?actor=${encodeURIComponent(session.actor)}&field=${mapped.field}`, { headers: { Origin: session.origin }, signal: abort.signal });
        if (!response.ok) throw new Error('one-time credential handoff was refused');
        const payload = await response.json(); value = payload?.value;
        if (typeof value !== 'string' || !value) throw new Error('one-time credential handoff was invalid');
        if (nodeId) await cdp.send('DOM.focus', { nodeId });
        else {
          const { x, y } = mapped.coordinates;
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0, pointerType: 'mouse' });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1, pointerType: 'mouse' });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1, pointerType: 'mouse' });
        }
        // Controlled React inputs in this Preview sometimes ignore a bulk
        // insertText update. Send real character events just as a person
        // typing into the focused visible field would, without ever logging
        // the characters.
        for (const character of value) {
          await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: character, text: character, unmodifiedText: character });
          await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: character });
        }
      } catch (error) {
        throw new Error(error?.name === 'AbortError' ? 'one-time credential handoff timed out' : 'one-time credential handoff did not fill the visible control');
      } finally { clearTimeout(timer); value = undefined; }
    }
    console.log(JSON.stringify({ status: 'visible_action_completed', actor: session.actor, feature: args.feature, evidence: mapped.evidence }));
  } finally { cdp.socket.close(); }
}
async function start() {
  if (!args.actor || !args.origin || !args.session) throw new Error(usage);
  const origin = assertOrigin(args.origin), initialPath = assertInitialPath(args.path); await access(CHROME, constants.X_OK);
  const profile = join(tmpdir(), `club-lab-sim-${args.actor}-${Date.now()}`); await mkdir(profile, { recursive: true });
  // Keep the DevTools endpoint loopback-only; the temporary profile is never the user's profile.
  // Fixed CSS geometry makes a reviewed coordinate map reproducible when an
  // app withholds DOM/AX trees from the isolated CDP attachment. Headless is
  // the normal Sim mode so shared desktop input cannot perturb a journey.
  // `--headed` is an explicit supervised research mode for the operator.
  const chromeArgs = [
    `--user-data-dir=${profile}`, '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
    '--window-size=1200,900', '--force-device-scale-factor=1',
  ];
  if (!headed) chromeArgs.push('--headless=new');
  chromeArgs.push(`${origin}${initialPath}`);
  // Preserve only Chrome's startup diagnostics beside the disposable profile.
  // The controller still never retains page contents, cookies, request bodies,
  // or credentials.  This gives a failed isolated launch an actionable local
  // record instead of silently falling back to a shared, headed browser.
  const diagnosticPath = `${resolve(args.session)}.startup.log`;
  await mkdir(dirname(diagnosticPath), { recursive: true });
  const diagnostic = await open(diagnosticPath, 'a', 0o600);
  const child = spawn(CHROME, chromeArgs, { detached: true, stdio: ['ignore', 'ignore', diagnostic.fd] });
  child.unref();
  let port;
  try { port = await waitForPort(profile); }
  catch (error) {
    await diagnostic.close();
    throw new Error(`${error.message}; isolated startup diagnostics retained locally`);
  }
  await diagnostic.close();
  const mode = headed ? 'headed_supervised' : 'headless_isolated';
  await saveSession(resolve(args.session), { schemaVersion: 1, actor: args.actor, origin, initialPath, profile, port, pid: child.pid, mode, diagnosticPath, createdAt: new Date().toISOString() });
  console.log(JSON.stringify({ status: 'isolated_browser_started', actor: args.actor, mode, session: resolve(args.session) }));
}
async function navigate() {
  const session = await loadSession();
  const path = assertInitialPath(args.path); const cdp = await attach(session);
  try {
    // Navigation stays on the already-approved product origin.  It does not
    // inspect or call an application API, and it preserves the visible
    // browser's authenticated session for ordinary deep links.
    await cdp.send('Page.bringToFront');
    await cdp.send('Page.navigate', { url: `${session.origin}${path}` });
    console.log(JSON.stringify({ status: 'visible_navigation_completed', actor: session.actor, path: redact(path) }));
  } finally { cdp.socket.close(); }
}
async function openPrivateInvite() {
  const session = await loadSession();
  if (!args.intent || !/^[a-z0-9:_-]+$/i.test(args.intent)) throw new Error('--intent is required');
  const mailboxPath = resolve(process.cwd(), 'state', 'secure-channel', 'recipient-mailboxes.json');
  const mailboxes = JSON.parse(await readFile(mailboxPath, 'utf8'));
  const mailbox = mailboxes?.[`${session.actor}:${args.intent}`];
  const invitation = mailbox?.invitationUrl;
  if (!mailbox || mailbox.status !== 'delivered' || mailbox.recipientActorId !== session.actor
    || mailbox.intentId !== args.intent || typeof invitation !== 'string') {
    throw new Error('no delivered private invitation is available for this actor');
  }
  const inviteUrl = new URL(invitation);
  if (inviteUrl.origin !== session.origin || !/^\/invites\/[A-Za-z0-9_-]+$/.test(inviteUrl.pathname)
    || inviteUrl.search || inviteUrl.hash) {
    throw new Error('private invitation is not on the approved product origin');
  }
  const cdp = await attach(session);
  try {
    await cdp.send('Page.bringToFront');
    await cdp.send('Page.navigate', { url: `${session.origin}${inviteUrl.pathname}` });
    console.log(JSON.stringify({ status: 'private_invitation_opened', actor: session.actor, intentId: args.intent }));
  } finally { cdp.socket.close(); }
}
async function snapshot() {
  const session = await loadSession(); const cdp = await attach(session);
  try {
    await cdp.send('Accessibility.enable');
    const tree = await cdp.send('Accessibility.getFullAXTree');
    const nodes = (tree.nodes ?? []).slice(0, 300).map((node) => ({
      role: node.role?.value ?? 'unknown',
      name: redact(node.name?.value ?? ''),
      disabled: Boolean(node.properties?.find((p) => p.name === 'disabled')?.value?.value),
      // A switch's rendered checked state is needed to distinguish a staff
      // confirmation screen from an actual staff-confirmed arrival. This is
      // structural UI state only, never a product API read.
      checked: node.properties?.find((p) => p.name === 'checked')?.value?.value ?? null,
    }));
    console.log(JSON.stringify({ status: 'snapshot', actor: session.actor, nodes }));
  } finally { cdp.socket.close(); }
}
async function domSnapshot() {
  // Structural inspection only: a fallback when Chrome does not expose its
  // accessibility tree. It never returns text content, values, URLs, or app
  // state; just enough stable control metadata to review the visible map.
  const session = await loadSession(); const cdp = await attach(session);
  try {
    await cdp.send('DOM.enable');
    const document = await cdp.send('DOM.getDocument', { depth: -1, pierce: true });
    const rows = [];
    const walk = (node) => {
      if (rows.length >= 120) return;
      const attrs = Array.isArray(node.attributes) ? node.attributes : [];
      const selected = {};
      for (let index = 0; index < attrs.length; index += 2) {
        if (['id', 'name', 'type', 'role', 'data-cy'].includes(attrs[index])) selected[attrs[index]] = redact(attrs[index + 1]);
      }
      if (Object.keys(selected).length || ['IFRAME', 'INPUT', 'BUTTON', 'FORM'].includes(node.nodeName)) rows.push({ nodeName: node.nodeName, attributes: selected, childCount: node.childNodeCount ?? 0 });
      for (const child of node.children ?? []) walk(child);
      for (const child of node.shadowRoots ?? []) walk(child);
      if (node.contentDocument) walk(node.contentDocument);
    };
    walk(document.root);
    console.log(JSON.stringify({ status: 'dom_snapshot', actor: session.actor, nodes: rows }));
  } finally { cdp.socket.close(); }
}
async function viewport() {
  const session = await loadSession(); const cdp = await attach(session);
  try {
    const metrics = await cdp.send('Page.getLayoutMetrics');
    const viewport = metrics.cssVisualViewport ?? metrics.visualViewport ?? {};
    console.log(JSON.stringify({ status: 'viewport', actor: session.actor, width: viewport.clientWidth ?? null, height: viewport.clientHeight ?? null, scale: viewport.scale ?? null, pageX: viewport.pageX ?? null, pageY: viewport.pageY ?? null }));
  } finally { cdp.socket.close(); }
}
async function waitLoginRedirect() {
  const session = await loadSession();
  const seconds = Math.max(1, Math.min(60, Number(args.seconds ?? 20)));
  const cdp = await attach(session);
  try {
    const deadline = Date.now() + seconds * 1000;
    let settledChecks = 0;
    while (Date.now() < deadline) {
      const tree = await cdp.send('Accessibility.getFullAXTree');
      const pending = (tree.nodes ?? []).some((node) => node.name?.value === 'Login successful. Redirecting...');
      const returnedToLogin = (tree.nodes ?? []).some((node) =>
        node.role?.value === 'textbox' && node.name?.value === 'Email' &&
        !node.properties?.some((property) => property.name === 'disabled' && property.value?.value === true));
      if (!pending && returnedToLogin) {
        console.log(JSON.stringify({ status: 'login_redirect_returned_to_login', actor: session.actor }));
        return;
      }
      // SPA transitions can briefly remove the redirect node while the next
      // render puts it back. Require three consecutive rendered checks so a
      // transient frame is never reported as a completed login redirect. An
      // enabled rendered Email control is explicitly a return to login; the
      // disabled loading form is still in-flight authentication.
      settledChecks = pending ? 0 : settledChecks + 1;
      if (settledChecks >= 3) {
        console.log(JSON.stringify({ status: 'login_redirect_settled', actor: session.actor }));
        return;
      }
      await sleep(1_000);
    }
    console.log(JSON.stringify({ status: 'login_redirect_pending', actor: session.actor, waitedSeconds: seconds }));
  } finally { cdp.socket.close(); }
}
async function waitFeature() {
  const session = await loadSession();
  if (!args.feature) throw new Error('--feature is required');
  const mapped = await feature(args.feature);
  const seconds = Math.max(1, Math.min(60, Number(args.seconds ?? 20)));
  const cdp = await attach(session);
  try {
    const deadline = Date.now() + seconds * 1000;
    while (Date.now() < deadline) {
      const tree = await cdp.send('Accessibility.getFullAXTree');
      const ready = (tree.nodes ?? []).some((node) =>
        node.role?.value === mapped.role &&
        (typeof mapped.namePrefix === 'string' ? node.name?.value?.startsWith(mapped.namePrefix) : node.name?.value === mapped.name) &&
        !node.properties?.some((property) => property.name === 'disabled' && property.value?.value === true));
      if (ready) {
        console.log(JSON.stringify({ status: 'visible_feature_ready', actor: session.actor, feature: args.feature }));
        return;
      }
      await sleep(500);
    }
    console.log(JSON.stringify({ status: 'visible_feature_pending', actor: session.actor, feature: args.feature, waitedSeconds: seconds }));
  } finally { cdp.socket.close(); }
}
async function captureInviteLink() {
  // A player can visibly copy a rendered generic invitation link. Capture it
  // from rendered AX text but never echo it: stdin carries it to the private
  // secure-channel store and stdout reports only safe metadata.
  const session = await loadSession();
  if (!args.intent) throw new Error('--intent is required');
  const cdp = await attach(session);
  try {
    await cdp.send('Accessibility.enable');
    const tree = await cdp.send('Accessibility.getFullAXTree');
    const host = new URL(session.origin).host.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matcher = new RegExp(`(?:https?:\\/\\/)?${host}\\/invites\\/[A-Za-z0-9_-]+`);
    const rendered = (tree.nodes ?? [])
      .map((node) => String(node.name?.value ?? ''))
      .map((name) => name.match(matcher)?.[0])
      .find(Boolean);
    if (!rendered) throw new Error('no visible invitation link is rendered');
    const rawUrl = rendered.startsWith('http') ? rendered : `https://${rendered}`;
    const result = await new Promise((resolveResult, rejectResult) => {
      const child = spawn('python', ['-m', 'podplay_sim_club.cli', 'secure-channel', 'retain-browser-invite', '--intent', args.intent, '--owner', session.actor, '--origin', session.origin], {
        cwd: process.cwd(), env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONPATH: 'src' }, stdio: ['pipe', 'pipe', 'pipe'],
      });
      let stdout = '', stderr = '';
      child.stdout.on('data', (chunk) => { stdout += chunk; });
      child.stderr.on('data', (chunk) => { stderr += chunk; });
      child.on('error', rejectResult);
      child.on('close', (code) => code === 0 ? resolveResult(stdout) : rejectResult(new Error(`secure invitation retention failed: ${redact(stderr)}`)));
      child.stdin.end(rawUrl);
    });
    const safe = JSON.parse(String(result));
    console.log(JSON.stringify({ status: 'visible_invite_link_retained', actor: session.actor, intentId: safe.intentId }));
  } finally { cdp.socket.close(); }
}
async function observe() {
  const session = await loadSession(); const seconds = Math.max(1, Math.min(120, Number(args.seconds ?? 15))); const out = resolve(args.out ?? join(tmpdir(), `club-lab-evidence-${Date.now()}`)); await mkdir(out, { recursive: true });
  const cdp = await attach(session); const events = []; const requested = new Map(); const requestDiagnostics = new Map(); const networkEvents = new Map();
  cdp.listen((message) => {
    const p = message.params ?? {};
    if (message.method === 'Network.requestWillBeSent') requested.set(p.requestId, { at: p.timestamp, method: p.request?.method, route: routeTemplate(p.request?.url) });
    // CDP's ExtraInfo events expose request/response header names.  Retain
    // only fixed booleans so the evidence can tell whether the browser
    // attempted session transport without ever recording a cookie, token, or
    // header value.
    if (message.method === 'Network.requestWillBeSentExtraInfo') {
      const headers = Object.keys(p.headers ?? {}).map((name) => name.toLowerCase());
      requestDiagnostics.set(p.requestId, {
        authorizationPresent: headers.includes('authorization'),
        cookiePresent: headers.includes('cookie'),
      });
      const event = networkEvents.get(p.requestId);
      if (event) event.authTransport = headers.includes('authorization') ? 'authorization_present' : headers.includes('cookie') ? 'cookie_present' : 'none_observed';
    }
    if (message.method === 'Network.responseReceivedExtraInfo') {
      const headers = Object.keys(p.headers ?? {}).map((name) => name.toLowerCase());
      const diagnostic = requestDiagnostics.get(p.requestId) ?? {};
      requestDiagnostics.set(p.requestId, {
        ...diagnostic,
        setCookiePresent: headers.includes('set-cookie'),
        wwwAuthenticatePresent: headers.includes('www-authenticate'),
      });
      const event = networkEvents.get(p.requestId);
      if (event) event.sessionResponse = headers.includes('set-cookie') ? 'set_cookie_present' : headers.includes('www-authenticate') ? 'www_authenticate_present' : 'none_observed';
    }
    if (message.method === 'Network.responseReceived') {
      const start = requested.get(p.requestId) ?? {}; const auth = requestDiagnostics.get(p.requestId) ?? {};
      const event = { kind: 'network', method: start.method ?? 'unknown', route: start.route ?? routeTemplate(p.response?.url), status: p.response?.status ?? null, durationMs: start.at ? Math.max(0, Math.round((p.timestamp - start.at) * 1000)) : null, authTransport: auth.authorizationPresent ? 'authorization_present' : auth.cookiePresent ? 'cookie_present' : 'none_observed', sessionResponse: auth.setCookiePresent ? 'set_cookie_present' : auth.wwwAuthenticatePresent ? 'www_authenticate_present' : 'none_observed' };
      events.push(event); networkEvents.set(p.requestId, event);
    }
    if (message.method === 'Network.loadingFailed') events.push({ kind: 'network_failure', route: requested.get(p.requestId)?.route ?? 'unknown_route', error: redact(p.errorText) });
    if (message.method === 'Log.entryAdded') events.push({ kind: 'console', level: p.entry?.level ?? 'unknown', source: p.entry?.source ?? 'unknown', text: redact(p.entry?.text) });
    if (message.method === 'Runtime.exceptionThrown') events.push({ kind: 'exception', text: redact(p.exceptionDetails?.text), line: p.exceptionDetails?.lineNumber ?? null, column: p.exceptionDetails?.columnNumber ?? null });
  });
  try {
    await Promise.all(['Page.enable', 'Network.enable', 'Log.enable', 'Runtime.enable'].map((method) => cdp.send(method)));
    await sleep(seconds * 1000);
    const image = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const screenshot = 'page.png'; await writeFile(join(out, screenshot), Buffer.from(image.data, 'base64'));
    // A compact operational card lets later journeys reuse recorded, safe
    // facts without relying on chat history or an unbounded reasoning trace.
    // Callers supply only plain-language rendered facts; redact again here so
    // the evidence contract remains authoritative at the write boundary.
    await cdp.send('Accessibility.enable');
    const tree = await cdp.send('Accessibility.getFullAXTree');
    const root = (tree.nodes ?? []).find((node) => node.role?.value === 'RootWebArea');
    const screen = redact(args['card-screen'] ?? root?.name?.value ?? 'rendered browser');
    const journeyCard = {
      screen,
      observed: redact(args['card-observed'] ?? 'bounded rendered observation captured'),
      next: redact(args['card-next'] ?? 'follow the active playbook or stop'),
      outcome: redact(args['card-outcome'] ?? 'observation only; no product outcome inferred'),
    };
    const evidence = { schemaVersion: 3, actor: session.actor, observedAt: new Date().toISOString(), durationSeconds: seconds, screenshot, journeyCard, events: events.slice(-200), privacy: 'no cookies, header values, request bodies, response bodies, raw URLs, or raw IDs retained; only fixed auth/session-presence categories are retained' };
    await writeFile(join(out, 'evidence.json'), JSON.stringify(evidence, null, 2), { mode: 0o600 }); console.log(JSON.stringify({ status: 'evidence_captured', actor: session.actor, out }));
  } finally {
    // Finish the CDP close handshake before Node exits. Without this, a
    // browser navigation can leave top-level await unsettled and discard the
    // bounded evidence bundle after the visible action has already occurred.
    if (cdp.socket.readyState === WebSocket.OPEN || cdp.socket.readyState === WebSocket.CONNECTING) {
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 250);
        const priorClose = cdp.socket.onclose;
        cdp.socket.onclose = (...event) => {
          clearTimeout(timer);
          if (typeof priorClose === 'function') priorClose(...event);
          resolve();
        };
        cdp.socket.close();
      });
    }
  }
}
async function captureQr() {
  const session = await loadSession();
  if (!args.out) throw new Error('capture-qr requires --out');
  const out = resolve(args.out);
  // A QR image can be an entry capability.  It is intentionally constrained
  // to a system temporary path and is consumed by the staff handoff helper,
  // never browser evidence or durable state.
  if (!out.startsWith(`${resolve(tmpdir())}/`)) throw new Error('QR capture must use a system temporary path');
  const cdp = await attach(session);
  try {
    await cdp.send('Page.bringToFront');
    // Decode the visible QR at native resolution instead of a full-page
    // screenshot, where a small modal code is too degraded for Vision. The
    // fixed accessible label comes from the rendered product QR modal; no
    // selector, page script, or QR payload leaves this routine.
    await cdp.send('DOM.enable');
    await cdp.send('Accessibility.enable');
    await cdp.send('DOM.getDocument');
    const tree = await cdp.send('Accessibility.getFullAXTree');
    const qr = (tree.nodes ?? []).find((node) => node.role?.value === 'image'
      && node.name?.value === 'QR Code' && Number.isInteger(node.backendDOMNodeId));
    let clip;
    if (qr?.backendDOMNodeId) {
      const pushed = await cdp.send('DOM.pushNodesByBackendIdsToFrontend', { backendNodeIds: [qr.backendDOMNodeId] });
      const nodeId = pushed.nodeIds?.[0];
      if (nodeId) {
        const model = await cdp.send('DOM.getBoxModel', { nodeId });
        const quad = model.model?.content;
        if (quad) {
          const left = Math.min(quad[0], quad[2], quad[4], quad[6]);
          const top = Math.min(quad[1], quad[3], quad[5], quad[7]);
          const right = Math.max(quad[0], quad[2], quad[4], quad[6]);
          const bottom = Math.max(quad[1], quad[3], quad[5], quad[7]);
          clip = { x: Math.max(0, left - 8), y: Math.max(0, top - 8), width: right - left + 16, height: bottom - top + 16, scale: 3 };
        }
      }
    }
    // Chrome can omit a canvas-backed image from a new AX attachment. The
    // full viewport fallback remains temporary and uses enough native pixels
    // for Vision to decode the rendered modal without retaining its payload.
    const image = await cdp.send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: false,
      ...(clip ? { clip } : { clip: { x: 0, y: 0, width: 1200, height: 900, scale: 3 } }),
    });
    await writeFile(out, Buffer.from(image.data, 'base64'), { mode: 0o600 });
    console.log(JSON.stringify({ status: 'ephemeral_qr_pixels_captured', actor: session.actor }));
  } finally { cdp.socket.close(); }
}
async function scanCode() {
  const session = await loadSession();
  // The dashboard's physical scanner emits this exact short keyboard payload.
  // Keep the value entirely in process memory: stdin is supplied by the local
  // QR helper and neither status output nor state files retain it.
  // `fs/promises.readFile` deliberately does not accept fd 0 on the Node
  // version used by these isolated journeys. Read the scanner bytes from the
  // process stream instead; the value remains process-local and is never
  // written or logged.
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  const value = input.trim();
  if (!/^userId=[A-Za-z0-9_-]{28}$/.test(value)) throw new Error('scanner payload is not an accepted QR handoff');
  const cdp = await attach(session);
  try {
    await cdp.send('Page.bringToFront');
    for (const character of value) {
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: character, text: character, unmodifiedText: character });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: character });
    }
    console.log(JSON.stringify({ status: 'scanner_input_dispatched', actor: session.actor }));
  } finally { cdp.socket.close(); }
}
async function close() {
  const session = await loadSession();
  // Chrome has helper processes; terminate the detached process group before
  // removing its profile, then let macOS release its small lock files.
  try { process.kill(-session.pid, 'SIGTERM'); } catch { try { process.kill(session.pid, 'SIGTERM'); } catch {} }
  await sleep(500);
  await rm(session.profile, { recursive: true, force: true, maxRetries: 8, retryDelay: 250 });
  await rm(resolve(args.session), { force: true });
  console.log(JSON.stringify({ status: 'isolated_browser_closed', actor: session.actor }));
}
try { if (command === 'start') await start(); else if (command === 'navigate') await navigate(); else if (command === 'open-private-invite') await openPrivateInvite(); else if (command === 'observe') await observe(); else if (command === 'snapshot') await snapshot(); else if (command === 'viewport') await viewport(); else if (command === 'dom-snapshot') await domSnapshot(); else if (command === 'wait-feature') await waitFeature(); else if (command === 'wait-login-redirect') await waitLoginRedirect(); else if (command === 'capture-invite-link') await captureInviteLink(); else if (command === 'capture-qr') await captureQr(); else if (command === 'scan-code') await scanCode(); else if (command === 'act') await act(); else if (command === 'close') await close(); else throw new Error(usage); } catch (error) { if (process.env.SIM_BROWSER_DEBUG === '1' && error instanceof Error) console.error(error.stack); fail(error instanceof Error ? error.message : String(error)); }
