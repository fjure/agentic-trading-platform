// Development infrastructure: read-only tests of pinned official MCP packages.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const servers = {
  context7: ['.tooling/node_modules/@upstash/context7-mcp/dist/index.js'],
  playwright: ['.tooling/node_modules/@playwright/mcp/cli.js', '--isolated', '--headless', '--browser', 'msedge', '--block-service-workers', '--no-webmcp', '--codegen', 'none'],
};
const name = process.argv[2];
if (!servers[name]) throw new Error('Choose context7 or playwright. No GitHub login or mutations supported.');
const args = [...servers[name]];
args[0] = path.join(root, args[0]);
const child = spawn(process.execPath, args, { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
let buffer = '';
let sequence = 0;
const pending = new Map();
child.stdout.setEncoding('utf8');
child.stderr.resume();
child.stdout.on('data', chunk => {
  buffer += chunk;
  let end;
  while ((end = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, end);
    buffer = buffer.slice(end + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    const waiter = pending.get(message.id);
    if (waiter) {
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
    }
  }
});
child.on('error', error => { for (const waiter of pending.values()) waiter.reject(error); });
child.on('exit', code => { for (const waiter of pending.values()) waiter.reject(new Error(`Server exited: ${code}`)); });
function request(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
}
const timeout = setTimeout(() => { child.kill(); console.error('Read-only smoke timeout'); process.exit(2); }, 25000);
try {
  const initialized = await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'repository-read-only-smoke', version: '1.0.0' } });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const list = await request('tools/list');
  console.log(JSON.stringify({ server: name, initialized: true, tools: list.tools.map(t => t.name) }));
  const result = name === 'context7'
    ? await request('tools/call', { name: 'resolve-library-id', arguments: { libraryName: 'FastAPI', query: 'Official FastAPI documentation' } })
    : await request('tools/call', { name: 'browser_navigate', arguments: { url: 'about:blank' } });
  console.log(JSON.stringify({ server: name, readOnlyCall: name === 'context7' ? 'resolve-library-id' : 'browser_navigate about:blank', isError: result.isError ?? false, summary: result.content?.filter(x => x.type === 'text').map(x => x.text.slice(0, 900)) }));
  if (result.isError) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ server: name, error: String(error) }));
  process.exitCode = 1;
} finally {
  child.stdin.end();
  child.kill();
  clearTimeout(timeout);
}
