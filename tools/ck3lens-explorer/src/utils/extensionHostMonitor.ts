import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

export type ExtensionHostGenerationStatus =
    | 'running'
    | 'deactivating'
    | 'clean_shutdown'
    | 'uncaught_exception'
    | 'process_exit';

export interface ExtensionHostGenerationRecord {
    schema_version: 1;
    generation_id: string;
    instance_id: string;
    extension_host_pid: number;
    status: ExtensionHostGenerationStatus;
    started_at: string;
    heartbeat_at: string;
    updated_at: string;
    extension_log_path: string;
    vscode_version: string;
    node_version: string;
    reason?: string;
    error?: {
        name: string;
        message: string;
        stack?: string;
    };
}

interface ExtensionHostMonitorOptions {
    dataRoot: string;
    instanceId: string;
    extensionLogPath: string;
    vscodeVersion: string;
    heartbeatIntervalMs?: number;
}

const DEFAULT_HEARTBEAT_INTERVAL_MS = 5_000;

function isoNow(): string {
    return new Date().toISOString();
}

function sanitizeInstanceId(instanceId: string): string {
    return instanceId.replace(/[^a-zA-Z0-9_-]/g, '_');
}

function serializeError(error: Error): ExtensionHostGenerationRecord['error'] {
    return {
        name: error.name,
        message: error.message,
        stack: error.stack?.slice(0, 4_000),
    };
}

function writeDurableFile(filePath: string, content: string): void {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const temporaryPath = `${filePath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    try {
        const descriptor = fs.openSync(temporaryPath, 'w');
        try {
            fs.writeFileSync(descriptor, content, 'utf8');
            fs.fsyncSync(descriptor);
        } finally {
            fs.closeSync(descriptor);
        }
        fs.renameSync(temporaryPath, filePath);
    } finally {
        try {
            fs.rmSync(temporaryPath, { force: true });
        } catch (error) {
            console.error('[CK3RAVEN] Failed to remove Extension Host snapshot temporary file', error);
        }
    }
}

export class ExtensionHostMonitor {
    private readonly statePath: string;
    private readonly heartbeatIntervalMs: number;
    private state: ExtensionHostGenerationRecord;
    private heartbeatTimer: NodeJS.Timeout | undefined;
    private completed = false;

    private readonly onUncaughtException = (error: Error): void => {
        this.updateState('uncaught_exception', {
            reason: 'uncaughtExceptionMonitor',
            error: serializeError(error),
        });
    };

    private readonly onProcessExit = (code: number): void => {
        if (this.completed) {
            return;
        }
        this.updateState('process_exit', { reason: `process exit code ${code}` });
    };

    constructor(options: ExtensionHostMonitorOptions) {
        const safeInstanceId = sanitizeInstanceId(options.instanceId);
        this.statePath = path.join(options.dataRoot, `extension_host_${safeInstanceId}.json`);
        this.heartbeatIntervalMs = options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS;

        const timestamp = isoNow();
        this.state = {
            schema_version: 1,
            generation_id: crypto.randomUUID(),
            instance_id: options.instanceId,
            extension_host_pid: process.pid,
            status: 'running',
            started_at: timestamp,
            heartbeat_at: timestamp,
            updated_at: timestamp,
            extension_log_path: options.extensionLogPath,
            vscode_version: options.vscodeVersion,
            node_version: process.version,
        };
    }

    start(): void {
        this.writeState();

        process.on('uncaughtExceptionMonitor', this.onUncaughtException);
        process.on('exit', this.onProcessExit);
        this.heartbeatTimer = setInterval(() => this.heartbeat(), this.heartbeatIntervalMs);
        this.heartbeatTimer.unref();

    }

    beginShutdown(): void {
        if (this.completed) {
            return;
        }
        this.updateState('deactivating', { reason: 'extension deactivate entered' });
    }

    markCleanShutdown(): void {
        if (this.completed) {
            return;
        }
        this.updateState('clean_shutdown', { reason: 'extension deactivate completed' });
        this.completed = true;
        this.disposeListeners();
    }

    private heartbeat(): void {
        if (this.completed) {
            return;
        }
        const timestamp = isoNow();
        this.state.heartbeat_at = timestamp;
        this.state.updated_at = timestamp;
        this.writeState();
    }

    private updateState(
        status: ExtensionHostGenerationStatus,
        details: Pick<ExtensionHostGenerationRecord, 'reason' | 'error'> = {},
    ): void {
        const timestamp = isoNow();
        this.state = {
            ...this.state,
            status,
            heartbeat_at: timestamp,
            updated_at: timestamp,
            ...details,
        };
        this.writeState();
    }

    private writeState(): void {
        try {
            writeDurableFile(this.statePath, `${JSON.stringify(this.state, null, 2)}\n`);
        } catch (error) {
            console.error('[CK3RAVEN] Failed to persist Extension Host generation state', error);
        }
    }

    private disposeListeners(): void {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = undefined;
        }
        process.off('uncaughtExceptionMonitor', this.onUncaughtException);
        process.off('exit', this.onProcessExit);
    }
}

export function startExtensionHostMonitor(
    options: ExtensionHostMonitorOptions,
): ExtensionHostMonitor {
    const monitor = new ExtensionHostMonitor(options);
    monitor.start();
    return monitor;
}
