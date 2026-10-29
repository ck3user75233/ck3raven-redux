const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const childProcess = require('node:child_process');
const { McpLifecycle } = require(path.join(process.env.MCP_TEST_BUILD, 'mcp', 'mcpLifecycle.js'));
const { McpProcess } = require(path.join(process.env.MCP_TEST_BUILD, 'mcp', 'mcpProcess.js'));

const generation = { instanceId: 'generation-one', sigilSecret: 'test-signing-key' };

function harness(publish) {
    const calls = [];
    const lifecycle = new McpLifecycle({
        publish: async (value, signal) => { calls.push(`publish:${value.instanceId}`); await publish?.(value, signal); },
        withdraw: () => { calls.push('withdraw'); },
        stopProcess: async () => { calls.push('stop'); return { pid: 123, forced: false, stopped: true }; },
        releaseClient: async () => { calls.push('release'); },
        removePublications: () => { calls.push('remove'); },
        disconnected: () => {},
        reportError: (_, error) => { throw error; },
    }, generation, () => ({ instanceId: 'generation-two', sigilSecret: 'new-key' }));
    return { lifecycle, calls };
}

test('start awaits readiness and startup failure uses common cleanup', async () => {
    let rejectLaunch;
    const pending = new Promise((_, reject) => { rejectLaunch = reject; });
    const { lifecycle, calls } = harness(() => pending);
    const start = lifecycle.start();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(lifecycle.state, 'starting');
    rejectLaunch(new Error('readiness failed'));
    await assert.rejects(start, /readiness failed/);
    assert.deepEqual(calls, ['publish:generation-one', 'withdraw', 'stop', 'release', 'remove']);
    assert.equal(lifecycle.state, 'stopped');
});

test('recovery serializes cleanup before fresh generation and close does not relaunch', async () => {
    const { lifecycle, calls } = harness();
    await lifecycle.start();
    lifecycle.connected('generation-one');
    await Promise.all([lifecycle.connectionLost('generation-one'), lifecycle.connectionLost('generation-one')]);
    assert.deepEqual(calls, [
        'publish:generation-one', 'withdraw', 'stop', 'release', 'remove', 'publish:generation-two',
    ]);
    lifecycle.connected('generation-two');
    await lifecycle.close();
    await lifecycle.connectionLost('generation-two');
    assert.equal(calls.filter(value => value.startsWith('publish:')).length, 2);
});

test('HTTP provider contains no command, environment, arguments, or signing key', () => {
    const originalLoad = Module._load;
    class HttpDefinition {
        constructor(label, uri, headers, version) { Object.assign(this, { label, uri, headers, version }); }
    }
    Module._load = function (id, ...args) {
        if (id === 'vscode') {
            return { McpHttpServerDefinition: HttpDefinition, Uri: { parse: value => value } };
        }
        return originalLoad.call(this, id, ...args);
    };
    try {
        const { CK3LensMcpServerProvider } = require(path.join(process.env.MCP_TEST_BUILD, 'mcp', 'mcpServerProvider.js'));
        const provider = new CK3LensMcpServerProvider(generation, {
            url: 'http://127.0.0.1:12345/mcp', token: 'transport-token',
        });
        const [definition] = provider.provideMcpServerDefinitions({});
        assert.equal(definition.uri, 'http://127.0.0.1:12345/mcp');
        assert.deepEqual(definition.headers, { 'x-ck3lens-token': 'transport-token' });
        assert.equal(definition.command, undefined);
        assert.equal(definition.env, undefined);
        assert.equal(definition.args, undefined);
        assert.ok(!JSON.stringify(definition).includes(generation.sigilSecret));
        provider.dispose();
        assert.deepEqual(provider.provideMcpServerDefinitions({}), []);
        assert.equal(provider.resolveMcpServerDefinition(definition, {}), undefined);
    } finally { Module._load = originalLoad; }
});

test('owned process is ready before return, survives client disconnect and stops on owner EOF', async () => {
    const originalSpawn = childProcess.spawn;
    const logger = { info() {}, error(message, error) { throw error ?? new Error(message); } };
    let exits = 0;
    let requested;
    childProcess.spawn = (executable, args, options) => {
        requested = args;
        return originalSpawn(executable, [
            path.join(process.env.MCP_TEST_REPO, 'tests', 'fixtures', 'mcp_http_fixture.py'),
        ], { ...options, env: {
            ...options.env,
            PYTHONPATH: path.join(process.env.MCP_TEST_REPO, 'tools', 'ck3lens_mcp'),
        } });
    };
    const owned = new McpProcess(process.env.MCP_TEST_REPO, process.env.MCP_TEST_PYTHON,
        process.env.MCP_TEST_REPO, logger, () => { exits++; });
    try {
        const endpoint = await owned.start(generation, 1, 30000);
        assert.deepEqual(requested, ['-m', 'tools.ck3lens_mcp.server', '--transport', 'streamable-http']);
        const headers = { 'x-ck3lens-token': endpoint.token };
        const first = await fetch(new URL('/health', endpoint.url), { headers });
        const health = await first.json();
        assert.equal(health.instance_id, generation.instanceId);
        const second = await fetch(new URL('/health', endpoint.url), { headers });
        assert.deepEqual(await second.json(), health);
        const result = await owned.stop();
        assert.equal(result.stopped, true);
        assert.equal(result.forced, false);
        assert.equal(result.pid, health.pid);
        assert.equal(exits, 0);
    } finally {
        await owned.stop();
        childProcess.spawn = originalSpawn;
    }
});

test('spawn failure rejects and owned cleanup completes', async () => {
    const owned = new McpProcess(process.env.MCP_TEST_REPO, path.join(process.env.MCP_TEST_REPO, 'missing-python.exe'),
        process.env.MCP_TEST_REPO, { info() {}, error() {} }, () => {});
    await assert.rejects(owned.start(generation, 1, 1000), /ENOENT/);
    assert.equal((await owned.stop()).stopped, true);
});

test('diagnostics accepts only the extension-owned generation token', () => {
    const { EventEmitter } = require('node:events');
    const originalLoad = Module._load;
    class VscodeEmitter {
        constructor() { this.listeners = []; this.event = fn => { this.listeners.push(fn); return { dispose() {} }; }; }
        fire(value) { this.listeners.forEach(fn => fn(value)); }
        dispose() {}
    }
    Module._load = function (id, ...args) {
        if (id === 'vscode') {
            return { EventEmitter: VscodeEmitter, workspace: { getConfiguration: () => ({ get: () => 0 }) } };
        }
        return originalLoad.call(this, id, ...args);
    };
    try {
        const { DiagnosticsServer } = require(path.join(process.env.MCP_TEST_BUILD, 'ipc', 'diagnosticsServer.js'));
        const diagnostics = new DiagnosticsServer({ info() {}, warn() {}, error() {}, debug() {} });
        diagnostics.setInstanceId('generation-one');
        diagnostics.expectOwnedProcess('owner-token');
        let accepted = 0;
        diagnostics.onMcpSubprocessStarted(() => { accepted++; });
        const socket = new EventEmitter();
        const hello = { instanceId: 'generation-one', pid: 123 };
        assert.equal(diagnostics.handleMcpStarted(hello, socket).ok, false);
        assert.equal(diagnostics.handleMcpStarted({ ...hello, ownerToken: 'wrong-token' }, socket).ok, false);
        assert.equal(diagnostics.handleMcpStarted({ ...hello, instanceId: 'retired', ownerToken: 'owner-token' }, socket).ok, false);
        assert.equal(accepted, 0);
        assert.equal(diagnostics.handleMcpStarted({ ...hello, ownerToken: 'owner-token' }, socket).ok, true);
        assert.equal(accepted, 1);
        assert.equal(diagnostics.handleMcpStarted({ ...hello, pid: 124, ownerToken: 'owner-token' }, new EventEmitter()).ok, false);
    } finally { Module._load = originalLoad; }
});

test('close during startup completes cleanup without publishing another generation', async () => {
    const { lifecycle, calls } = harness((_, signal) => new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('owner cancelled startup')), { once: true });
    }));
    const start = lifecycle.start();
    await new Promise(resolve => setImmediate(resolve));
    const closing = lifecycle.close();
    await assert.rejects(start, /owner cancelled startup/);
    await closing;
    assert.equal(lifecycle.state, 'stopped');
    assert.deepEqual(calls, ['publish:generation-one', 'withdraw', 'stop', 'release', 'remove']);
});

test('unconfirmed process stop blocks replacement publication', async () => {
    let publications = 0;
    const lifecycle = new McpLifecycle({
        publish() { publications++; },
        withdraw() {},
        stopProcess: async () => ({ pid: 123, forced: true, stopped: false }),
        releaseClient() { throw new Error('must not release a live process'); },
        removePublications() { throw new Error('must not remove a live process'); },
        disconnected() {},
        reportError() {},
    }, generation);
    await lifecycle.start();
    lifecycle.connected(generation.instanceId);
    await assert.rejects(lifecycle.connectionLost(generation.instanceId), /cleanup incomplete/);
    assert.equal(lifecycle.state, 'failed');
    assert.equal(publications, 1);
});

test('foreign readiness URL is rejected and failed startup process is reaped', async () => {
    const originalSpawn = childProcess.spawn;
    childProcess.spawn = (executable, args, options) => originalSpawn(executable, ['-c', [
        'import json,os,time',
        'from ck3lens.http_transport import start_owner_monitor',
        'start_owner_monitor()',
        'print(\'CK3LENS_HTTP_READY \'+json.dumps({"instance_id":"generation-one","pid":os.getpid(),"url":"http://192.0.2.1:1234/mcp"}),flush=True)',
        'time.sleep(60)',
    ].join(';')], { ...options, env: { ...options.env,
        PYTHONPATH: path.join(process.env.MCP_TEST_REPO, 'tools', 'ck3lens_mcp'),
    } });
    const owned = new McpProcess(process.env.MCP_TEST_REPO, process.env.MCP_TEST_PYTHON,
        process.env.MCP_TEST_REPO, { info() {}, error() {} }, () => {});
    try {
        await assert.rejects(owned.start(generation, 1, 15000), /loopback HTTP endpoint/);
        assert.equal((await owned.stop()).stopped, true);
    } finally { await owned.stop(); childProcess.spawn = originalSpawn; }
});

test('missing readiness times out and cleanup reaps the owned process', async () => {
    const originalSpawn = childProcess.spawn;
    childProcess.spawn = (executable, args, options) => originalSpawn(executable, ['-c', [
        'import time',
        'from ck3lens.http_transport import start_owner_monitor',
        'start_owner_monitor()',
        'time.sleep(60)',
    ].join(';')], { ...options, env: { ...options.env,
        PYTHONPATH: path.join(process.env.MCP_TEST_REPO, 'tools', 'ck3lens_mcp'),
    } });
    const owned = new McpProcess(process.env.MCP_TEST_REPO, process.env.MCP_TEST_PYTHON,
        process.env.MCP_TEST_REPO, { info() {}, error() {} }, () => {});
    try {
        await assert.rejects(owned.start(generation, 1, 5000), /readiness timed out/);
        assert.equal((await owned.stop()).stopped, true);
    } finally { await owned.stop(); childProcess.spawn = originalSpawn; }
});
