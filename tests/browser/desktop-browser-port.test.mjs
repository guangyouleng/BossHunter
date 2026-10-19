import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

// Run the actual bridge entry point with local transport doubles. No browser,
// listening socket, user's profile, or external website is accessed.
const runtime = new URL('../../src/bosshunter/browser/runtime/cdp-proxy.mjs', import.meta.url).href;
const fixture = `
import fs from 'node:fs';
import os from 'node:os';
import net from 'node:net';
import http from 'node:http';
import { EventEmitter } from 'node:events';
os.platform = () => 'win32';
const read = fs.readFileSync;
fs.readFileSync = (path, ...args) => typeof path === 'string' && path.endsWith('DevToolsActivePort') ? '9333\\n/devtools/browser/other\\n' : read(path, ...args);
net.createConnection = port => {
  const socket = new EventEmitter(); socket.destroy = () => {};
  queueMicrotask(() => socket.emit(process.env.FIXTURE_PORT_MISSING === String(port) ? 'error' : 'connect'));
  return socket;
};
http.createServer = net.createServer = () => {
  const server = new EventEmitter(); server.close = () => {};
  server.listen = (port, host, callback) => {
    queueMicrotask(() => { server.emit('listening'); callback?.(); });
    return server;
  };
  return server;
};
globalThis.fetch = async url => ({ ok: true, json: async () => ({Browser:'Chrome/146', webSocketDebuggerUrl:'ws://127.0.0.1:' + new URL(url).port + '/devtools/browser/chosen'}) });
globalThis.WebSocket = class {
  constructor(url) { console.log('TEST_SOCKET=' + url); process.exit(0); }
};
setTimeout(() => process.exit(2), 250);
await import(${JSON.stringify(runtime)});
`;
function run(strict, missing = '') {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', fixture], {
    env: {...process.env, BOSSHUNTER_CHROME_PORTS:'9222', BOSSHUNTER_CHROME_PORT_STRICT:strict,
      FIXTURE_PORT_MISSING:missing}, encoding:'utf8', timeout:5000,
  });
  assert.ifError(result.error);
  return result;
}
test('desktop connects only to the selected browser port despite another active profile', () => {
  const result = run('true');
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /TEST_SOCKET=ws:\/\/127\.0\.0\.1:9222\//);
});
test('desktop does not fall back to ordinary Chrome when the selected browser is unavailable', () => {
  const result = run('true', '9222');
  assert.equal(result.status, 2);
  assert.doesNotMatch(result.stdout, /TEST_SOCKET=/);
  assert.match(result.stderr, /Chrome debug port not found/);
});
test('source-mode automatic discovery remains available without strict desktop mode', () => {
  const result = run('false');
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /TEST_SOCKET=ws:\/\/127\.0\.0\.1:9333\//);
});
