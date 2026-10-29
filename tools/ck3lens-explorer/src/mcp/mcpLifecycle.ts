import * as crypto from 'crypto';

export interface McpGeneration {
    readonly instanceId: string;
    readonly sigilSecret: string;
}

export interface McpProcessStopResult {
    pid: number | null;
    forced: boolean;
    stopped: boolean;
}

export function createMcpGeneration(): McpGeneration {
    return {
        instanceId: `${Date.now().toString(36).slice(-4)}-${crypto.randomBytes(3).toString('hex')}`,
        sigilSecret: crypto.randomBytes(16).toString('hex'),
    };
}

export interface McpLifecycleHooks {
    publish(generation: McpGeneration, signal: AbortSignal): void | Promise<void>;
    withdraw(): void;
    stopProcess(instanceId: string): Promise<McpProcessStopResult>;
    removePublications(instanceId: string): void;
    releaseClient(instanceId: string): Promise<void>;
    disconnected(): void;
    reportError(step: string, error: unknown): void;
    cleanupCompleted?(instanceId: string, process: McpProcessStopResult): void;
}

export class McpLifecycle {
    private generation: McpGeneration;
    private phase: 'stopped' | 'starting' | 'running' | 'cleaning' | 'failed' = 'stopped';
    private closing = false;
    private usedGeneration = false;
    private operation: Promise<void> = Promise.resolve();
    private starting?: Promise<void>;
    private cleaning?: Promise<void>;
    private recovering?: Promise<void>;
    private startupAbort?: AbortController;

    constructor(
        private readonly hooks: McpLifecycleHooks,
        initialGeneration: McpGeneration = createMcpGeneration(),
        private readonly createGeneration: () => McpGeneration = createMcpGeneration,
    ) {
        this.generation = initialGeneration;
    }

    get current(): McpGeneration {
        return this.generation;
    }

    get state(): string {
        return this.phase;
    }

    start(): Promise<void> {
        if (this.closing) {
            return Promise.reject(new Error('Cannot start MCP while the extension is closing'));
        }
        if (this.starting) {
            return this.starting;
        }
        const operation = this.enqueue(async () => {
            if (this.closing || this.phase === 'starting' || this.phase === 'running') {
                return;
            }
            if (this.phase !== 'stopped') {
                throw new Error('MCP cleanup must complete before start');
            }
            if (this.usedGeneration) {
                this.generation = this.createGeneration();
            }
            this.usedGeneration = true;
            this.phase = 'starting';
            const startupAbort = new AbortController();
            this.startupAbort = startupAbort;
            try {
                await this.hooks.publish(this.generation, startupAbort.signal);
            } catch (error) {
                this.phase = 'failed';
                try {
                    // A failed launch may already own a process or a lifecycle lease.
                    await this.performCleanup();
                } catch (cleanupError) {
                    throw new AggregateError([error, cleanupError], 'MCP start and cleanup failed');
                }
                throw error;
            } finally {
                this.startupAbort = undefined;
            }
        });
        this.starting = operation.finally(() => { this.starting = undefined; });
        return this.starting;
    }

    connected(instanceId: string): void {
        if (instanceId !== this.generation.instanceId || this.phase !== 'starting' || this.closing) {
            throw new Error('MCP hello does not belong to the starting generation');
        }
        this.phase = 'running';
    }

    cleanup(): Promise<void> {
        if (this.cleaning) {
            return this.cleaning;
        }
        const operation = this.enqueue(() => this.performCleanup());
        this.cleaning = operation.finally(() => { this.cleaning = undefined; });
        return this.cleaning;
    }

    private async performCleanup(): Promise<void> {
            if (this.phase === 'stopped') {
                return;
            }
            this.phase = 'cleaning';
            const failures: Error[] = [];
            const attempt = async (step: string, action: () => void | Promise<void>): Promise<void> => {
                try {
                    await action();
                } catch (error) {
                    failures.push(new Error(`${step}: ${error instanceof Error ? error.message : String(error)}`, { cause: error }));
                }
            };
            try {
                this.hooks.disconnected();
            } catch (error) {
                this.hooks.reportError('updating disconnected display', error);
            }
            await attempt('withdrawing MCP registration', () => this.hooks.withdraw());
            let stopped: McpProcessStopResult | undefined;
            await attempt('stopping MCP process', async () => {
                const result = await this.hooks.stopProcess(this.generation.instanceId);
                if (!result.stopped) {
                    throw new Error(`MCP process exit could not be confirmed: pid=${result.pid}`);
                }
                stopped = result;
            });
            if (stopped) {
                await attempt('releasing MCP QBuilder lease', () => this.hooks.releaseClient(this.generation.instanceId));
                await attempt('removing MCP publications', () => this.hooks.removePublications(this.generation.instanceId));
            }
            if (failures.length) {
                this.phase = 'failed';
                throw new AggregateError(failures, `MCP cleanup incomplete: ${failures.map(error => error.message).join('; ')}`);
            }
            this.phase = 'stopped';
            if (stopped) {
                try {
                    this.hooks.cleanupCompleted?.(this.generation.instanceId, stopped);
                } catch (error) {
                    this.hooks.reportError('logging completed MCP cleanup', error);
                }
            }
    }

    connectionLost(instanceId: string): Promise<void> {
        if (instanceId !== this.generation.instanceId || this.closing || this.phase === 'cleaning') {
            return Promise.resolve();
        }
        if (!this.recovering) {
            this.recovering = (async () => {
                await this.cleanup();
                if (!this.closing) {
                    await this.start();
                }
            })().finally(() => { this.recovering = undefined; });
        }
        return this.recovering;
    }

    close(): Promise<void> {
        this.closing = true;
        this.startupAbort?.abort(new Error('Extension closed during MCP startup'));
        return this.cleanup();
    }

    private enqueue(action: () => Promise<void>): Promise<void> {
        const operation = this.operation.then(action);
        // Keep the queue usable after a reported failure; callers still receive it.
        this.operation = operation.then(() => undefined, () => undefined);
        return operation;
    }
}
