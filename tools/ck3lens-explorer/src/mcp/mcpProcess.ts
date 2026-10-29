import { ChildProcessWithoutNullStreams, execFile, spawn } from 'child_process';
import * as crypto from 'crypto';
import * as http from 'http';
import * as path from 'path';
import * as readline from 'readline';
import { McpGeneration, McpProcessStopResult } from './mcpLifecycle';

export interface McpHttpEndpoint {
    readonly url: string;
    readonly token: string;
}

interface ProcessLogger {
    info(message: string): void;
    error(message: string, error?: unknown): void;
}

export class McpProcess {
    private child?: ChildProcessWithoutNullStreams;
    private closed?: Promise<void>;
    private stopping = false;
    private failed = false;
    private actualPid: number | null = null;
    private readonly token = crypto.randomBytes(32).toString('hex');

    constructor(
        private readonly rootRepo: string,
        private readonly pythonPath: string,
        private readonly dataRoot: string,
        private readonly logger: ProcessLogger,
        private readonly onExit: (instanceId: string, code: number | null, signal: NodeJS.Signals | null) => void,
    ) {}

    getOwnerToken(): string {
        return this.token;
    }

    async start(generation: McpGeneration, ipcPort: number, timeoutMs = 120_000, signal?: AbortSignal): Promise<McpHttpEndpoint> {
        signal?.throwIfAborted();
        if (this.child) {
            throw new Error('An MCP process is already owned');
        }
        const scriptsDir = path.dirname(this.pythonPath);
        const child = spawn(this.pythonPath, ['-m', 'tools.ck3lens_mcp.server', '--transport', 'streamable-http'], {
            cwd: this.rootRepo,
            windowsHide: true,
            stdio: 'pipe',
            env: {
                ...process.env,
                CK3LENS_INSTANCE_ID: generation.instanceId,
                CK3LENS_SIGIL_SECRET: generation.sigilSecret,
                CK3LENS_HTTP_TOKEN: this.token,
                CK3LENS_IPC_PORT: String(ipcPort),
                CK3RAVEN_DATA: this.dataRoot,
                PYTHONPATH: [this.rootRepo, path.join(this.rootRepo, 'src')].join(path.delimiter),
                PYTHONUNBUFFERED: '1',
                PYTHONFAULTHANDLER: '1',
                PATH: scriptsDir + path.delimiter + (process.env.PATH ?? ''),
                VIRTUAL_ENV: path.dirname(scriptsDir),
            },
        });
        this.child = child;
        child.stdin.on('error', error => this.logger.error('Owned MCP lifetime pipe error', error));
        // Keep a permanent error listener even after readiness has settled.
        child.on('error', error => {
            this.failed = true;
            this.logger.error('Owned MCP process error', error);
        });
        this.closed = new Promise(resolve => {
            child.once('close', (code, signal) => {
                this.failed = true;
                this.logger.info(`Owned MCP process closed: instanceId=${generation.instanceId} launcherPid=${child.pid} pid=${this.actualPid} code=${code} signal=${signal}`);
                resolve();
                if (!this.stopping) {
                    this.onExit(generation.instanceId, code, signal);
                }
            });
        });
        const stdout = readline.createInterface({ input: child.stdout });
        const stderr = readline.createInterface({ input: child.stderr });
        stderr.on('line', line => this.logger.info(`[MCP ${generation.instanceId}] ${line}`));
        this.closed.then(() => { stdout.close(); stderr.close(); });

        return new Promise((resolve, reject) => {
            let settled = false;
            const finish = (error?: Error, endpoint?: McpHttpEndpoint): void => {
                if (settled) { return; }
                settled = true;
                clearTimeout(timer);
                child.removeListener('error', onError);
                child.removeListener('close', onClose);
                signal?.removeEventListener('abort', onAbort);
                if (error) { reject(error); }
                else if (endpoint) { resolve(endpoint); }
            };
            const onError = (error: Error): void => {
                this.failed = true;
                finish(error);
            };
            const onClose = (): void => finish(new Error('Owned MCP exited before HTTP readiness'));
            const onAbort = (): void => finish(new Error('MCP startup cancelled by owner'));
            const timer = setTimeout(() => finish(new Error(`MCP HTTP readiness timed out after ${timeoutMs}ms`)), timeoutMs);
            signal?.addEventListener('abort', onAbort, { once: true });
            child.on('error', onError);
            child.once('close', onClose);
            stdout.on('line', (line: string) => {
                const prefix = 'CK3LENS_HTTP_READY ';
                if (!line.startsWith(prefix)) {
                    this.logger.info(`[MCP ${generation.instanceId}] ${line}`);
                    return;
                }
                if (settled) { return; }
                void (async () => {
                    const value: unknown = JSON.parse(line.slice(prefix.length));
                    if (typeof value !== 'object' || value === null
                        || !('instance_id' in value) || value.instance_id !== generation.instanceId
                        || !('pid' in value) || typeof value.pid !== 'number' || !Number.isInteger(value.pid) || value.pid <= 0
                        || !('url' in value) || typeof value.url !== 'string') {
                        throw new Error('Invalid MCP HTTP readiness announcement');
                    }
                    const url = new URL(value.url);
                    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port
                        || url.pathname !== '/mcp' || url.username || url.password || url.search || url.hash) {
                        throw new Error('MCP readiness must advertise a loopback HTTP endpoint');
                    }
                    this.actualPid = value.pid;
                    await this.probe(url, generation.instanceId, value.pid);
                    if (this.failed || this.stopping) {
                        throw new Error('MCP exited or stopped during readiness verification');
                    }
                    finish(undefined, { url: url.href, token: this.token });
                })().catch(error => finish(error instanceof Error ? error : new Error(String(error))));
            });
        });
    }

    async stop(): Promise<McpProcessStopResult> {
        this.stopping = true;
        const child = this.child;
        if (!child || !this.closed) {
            return { pid: null, forced: false, stopped: true };
        }
        // EOF is an owner-lifetime signal, never an MCP client disconnect.
        child.stdin.end();
        let forced = false;
        if (!await this.waitForClose(1500)) {
            forced = true;
            if (!child.pid) { throw new Error('Owned MCP has no launcher PID for termination'); }
            if (process.platform === 'win32') {
                await new Promise<void>((resolve, reject) => {
                    execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, error => {
                        if (error && child.exitCode === null) { reject(error); }
                        else { resolve(); }
                    });
                });
            } else {
                child.kill('SIGKILL');
            }
        }
        const stopped = await this.waitForClose(3000);
        return { pid: this.actualPid ?? child.pid ?? null, forced, stopped };
    }

    private async waitForClose(timeoutMs: number): Promise<boolean> {
        let timer: NodeJS.Timeout | undefined;
        try {
            return await Promise.race([
                this.closed!.then(() => true),
                new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), timeoutMs); }),
            ]);
        } finally {
            if (timer) { clearTimeout(timer); }
        }
    }

    private probe(url: URL, instanceId: string, pid: number): Promise<void> {
        return new Promise((resolve, reject) => {
            const request = http.get(new URL('/health', url), {
                headers: { 'x-ck3lens-token': this.token },
                timeout: 3000,
            }, response => {
                let body = '';
                response.setEncoding('utf8');
                response.on('data', chunk => { body += chunk; });
                response.on('error', reject);
                response.on('end', () => {
                    try {
                        const health = JSON.parse(body);
                        if (response.statusCode !== 200 || health.instance_id !== instanceId || health.pid !== pid) {
                            throw new Error('MCP HTTP health does not match the owned generation');
                        }
                        resolve();
                    } catch (error) { reject(error); }
                });
            });
            request.once('timeout', () => request.destroy(new Error('MCP health probe timed out')));
            request.once('error', reject);
        });
    }
}
