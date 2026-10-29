import * as childProcess from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as net from 'net';
import * as path from 'path';
import { Logger } from '../utils/logger';
import { CK3RAVEN_DATA_ROOT } from '../config/runtimePaths';

const PROTOCOL_VERSION = 1;
const STARTUP_TIMEOUT_MS = 120_000;
const REQUEST_TIMEOUT_MS = 3_000;
const HEARTBEAT_INTERVAL_MS = 30_000;
const RETRY_INTERVAL_MS = 200;
const CONTENDER_RETRY_MS = 6_000;

interface EndpointRecord {
    host: string;
    port: number;
    pid: number;
    run_id: string;
    db_path: string;
    protocol: number;
}

interface IpcResponse {
    ok: boolean;
    result?: Record<string, unknown>;
    error?: {
        code?: string;
        message?: string;
    };
}

function delay(milliseconds: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

export class QBuilderSupervisor {
    private readonly endpointPath = path.join(CK3RAVEN_DATA_ROOT, 'qbuilder_endpoint.json');
    private readonly clientId: string;
    private heartbeatTimer: NodeJS.Timeout | undefined;
    private ensurePromise: Promise<Record<string, unknown>> | undefined;
    private stopped = false;

    constructor(
        private readonly rootRepo: string,
        private readonly pythonPath: string,
        instanceId: string,
        private readonly logger: Logger,
    ) {
        this.clientId = `extension:${instanceId}`;
    }

    async start(): Promise<Record<string, unknown>> {
        const health = await this.ensureReady();
        this.logger.info(
            `QBuilder supervisor attached: clientId=${this.clientId} `
            + `extensionHostPid=${process.pid} daemonPid=${String(health['daemon_pid'])} `
            + `runId=${String(health['run_id'])} port=${String(health['ipc_port'])} `
            + `endpoint=${this.endpointPath}`
        );
        this.heartbeatTimer = setInterval(() => {
            void this.ensureReady().catch(error => {
                this.logger.error('QBuilder heartbeat recovery failed', error);
            });
        }, HEARTBEAT_INTERVAL_MS);
        return health;
    }

    async unregisterMcpClient(instanceId: string): Promise<void> {
        await this.request('unregister_client', {}, instanceId);
    }

    async stop(): Promise<void> {
        if (this.stopped) {
            return;
        }
        this.stopped = true;
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = undefined;
        }

        try {
            await this.request('unregister_client', {}, this.clientId);
        } catch (error) {
            this.logger.debug(`Could not unregister extension QBuilder lease: ${String(error)}`);
        }

        try {
            const result = await this.request('shutdown', { graceful: true }, this.clientId);
            if (result['acknowledged'] === true) {
                this.logger.info('QBuilder shutdown acknowledged for last extension window');
            } else {
                this.logger.info('QBuilder remains active for another extension window');
            }
        } catch (error) {
            this.logger.debug(`QBuilder shutdown request was unavailable: ${String(error)}`);
        }
    }

    private ensureReady(): Promise<Record<string, unknown>> {
        if (!this.ensurePromise) {
            this.ensurePromise = this.ensureReadyInternal().finally(() => {
                this.ensurePromise = undefined;
            });
        }
        return this.ensurePromise;
    }

    private async ensureReadyInternal(): Promise<Record<string, unknown>> {
        try {
            return await this.health();
        } catch {
            this.logger.info('QBuilder unavailable; starting a writer-lock contender');
        }

        let contender = this.spawnContender();
        let nextContenderAt = Date.now() + CONTENDER_RETRY_MS;
        const deadline = Date.now() + STARTUP_TIMEOUT_MS;
        let lastError: unknown;

        while (Date.now() < deadline) {
            try {
                const health = await this.health();
                this.logger.info(
                    `QBuilder ready: pid=${String(health['daemon_pid'])} ` +
                    `run_id=${String(health['run_id'])} port=${String(health['ipc_port'])}`
                );
                return health;
            } catch (error) {
                lastError = error;
            }

            if (Date.now() >= nextContenderAt && contender.exitCode !== null) {
                contender = this.spawnContender();
                nextContenderAt = Date.now() + CONTENDER_RETRY_MS;
            }
            await delay(RETRY_INTERVAL_MS);
        }

        throw new Error(`QBuilder did not become healthy within 120 seconds: ${String(lastError)}`);
    }

    private spawnContender(): childProcess.ChildProcess {
        const logDir = path.join(CK3RAVEN_DATA_ROOT, 'logs');
        fs.mkdirSync(logDir, { recursive: true });
        const logPath = path.join(logDir, 'qbuilder_provider.log');
        const logFd = fs.openSync(logPath, 'a');

        try {
            const contender = childProcess.spawn(
                this.pythonPath,
                ['-u', '-m', 'qbuilder.cli', 'daemon'],
                {
                    cwd: this.rootRepo,
                    detached: true,
                    windowsHide: true,
                    stdio: ['ignore', logFd, logFd],
                    env: {
                        ...process.env,
                        CK3RAVEN_DATA: CK3RAVEN_DATA_ROOT,
                        PYTHONPATH: `${this.rootRepo}${path.delimiter}${path.join(this.rootRepo, 'src')}`,
                        PYTHONUNBUFFERED: '1',
                        PYTHONFAULTHANDLER: '1',
                    },
                },
            );
            contender.once('error', error => {
                this.logger.error('QBuilder contender failed to spawn', error);
            });
            contender.once('exit', code => {
                this.logger.debug(`QBuilder contender exited with code ${String(code)}`);
            });
            contender.unref();
            return contender;
        } finally {
            fs.closeSync(logFd);
        }
    }

    private readEndpoint(): EndpointRecord {
        const raw = fs.readFileSync(this.endpointPath, 'utf8');
        const endpoint = JSON.parse(raw) as Partial<EndpointRecord>;
        if (
            endpoint.host !== '127.0.0.1' ||
            !Number.isInteger(endpoint.port) ||
            (endpoint.port ?? 0) < 1 ||
            (endpoint.port ?? 0) > 65_535 ||
            !Number.isInteger(endpoint.pid) ||
            (endpoint.pid ?? 0) < 1 ||
            typeof endpoint.run_id !== 'string' ||
            endpoint.run_id.length === 0 ||
            typeof endpoint.db_path !== 'string' ||
            endpoint.db_path.length === 0 ||
            endpoint.protocol !== PROTOCOL_VERSION
        ) {
            throw new Error('Published QBuilder endpoint is invalid');
        }
        return endpoint as EndpointRecord;
    }

    private async health(): Promise<Record<string, unknown>> {
        const endpoint = this.readEndpoint();
        const result = await this.requestAt(endpoint, 'health', {}, this.clientId);
        const identity = [result['daemon_pid'], result['run_id'], result['ipc_port']];
        const expected = [endpoint.pid, endpoint.run_id, endpoint.port];
        if (identity.some((value, index) => value !== expected[index])) {
            throw new Error('QBuilder health identity does not match the published endpoint');
        }
        return result;
    }

    private async request(
        method: string,
        params: Record<string, unknown>,
        clientId: string,
    ): Promise<Record<string, unknown>> {
        return this.requestAt(this.readEndpoint(), method, params, clientId);
    }

    private requestAt(
        endpoint: EndpointRecord,
        method: string,
        params: Record<string, unknown>,
        clientId: string,
    ): Promise<Record<string, unknown>> {
        const request = JSON.stringify({
            v: PROTOCOL_VERSION,
            id: crypto.randomUUID().slice(0, 8),
            method,
            params: {
                ...params,
                client_id: clientId,
                client_pid: process.pid,
                client_name: `ck3lens-${clientId}`,
            },
        }) + '\n';

        return new Promise((resolve, reject) => {
            const socket = net.createConnection({ host: endpoint.host, port: endpoint.port });
            let buffer = '';
            let settled = false;

            const fail = (error: Error): void => {
                if (settled) {
                    return;
                }
                settled = true;
                socket.destroy();
                reject(error);
            };

            socket.setTimeout(REQUEST_TIMEOUT_MS);
            socket.once('connect', () => socket.write(request));
            socket.on('data', chunk => {
                buffer += chunk.toString('utf8');
                const newline = buffer.indexOf('\n');
                if (newline < 0 || settled) {
                    return;
                }
                settled = true;
                socket.end();
                try {
                    const response = JSON.parse(buffer.slice(0, newline)) as IpcResponse;
                    if (!response.ok) {
                        throw new Error(
                            `${response.error?.code ?? 'UNKNOWN'}: ` +
                            `${response.error?.message ?? 'QBuilder request failed'}`
                        );
                    }
                    resolve(response.result ?? {});
                } catch (error) {
                    reject(error);
                }
            });
            socket.once('timeout', () => fail(new Error('QBuilder request timed out')));
            socket.once('error', fail);
            socket.once('close', () => {
                if (!settled) {
                    fail(new Error('QBuilder closed the connection before replying'));
                }
            });
        });
    }
}
