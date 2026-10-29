/**
 * Diagnostics IPC Server
 * 
 * TCP server that exposes VS Code diagnostics and other IDE APIs to external tools (like MCP server).
 * This allows the MCP server to query VS Code's language server diagnostics, Pylance errors, etc.
 * 
 * Protocol: JSON-RPC over TCP
 * Default port: 9847 (configurable via ck3lens.ipcPort setting)
 */

import * as net from 'net';
import * as crypto from 'crypto';
import * as vscode from 'vscode';
import { Logger } from '../utils/logger';
import { CK3RAVEN_DATA_ROOT, getAgentModeFilePath, getIpcPortFilePath } from '../config/runtimePaths';

interface JsonRpcRequest {
    jsonrpc: '2.0';
    id: number | string;
    method: string;
    params?: any;
}

interface JsonRpcResponse {
    jsonrpc: '2.0';
    id: number | string;
    result?: any;
    error?: {
        code: number;
        message: string;
        data?: any;
    };
}

/**
 * Pending script signing request tracked in memory
 */
interface PendingScriptSign {
    path: string;
    canonical_path: string;  // Session-absolute canonical address for HMAC payload
    content_sha256: string;
    status: 'pending' | 'approved' | 'rejected';
    signature?: string;
    signed_at?: string;
    codeLensDisposable?: vscode.Disposable;
    codeLensProvider?: ScriptSignCodeLensProvider;
}

export class DiagnosticsServer implements vscode.Disposable {
    private server: net.Server | null = null;
    private starting?: Promise<void>;
    private readonly port: number;
    private readonly host: string = '127.0.0.1';
    private clients: Set<net.Socket> = new Set();
    private getSigilSecret?: () => string;
    private instanceId?: string;
    private acceptingStarts = true;
    private ownerToken?: string;

    /** In-memory pending script signing requests, keyed by host-absolute path (internal I/O boundary only) */
    private pendingScriptSigns: Map<string, PendingScriptSign> = new Map();
    /** Disposables for scriptSign commands — stored so they're properly removed on dispose(). */
    private static scriptSignCommandDisposables: vscode.Disposable[] = [];

    /** Retained until cleanup confirms exit, independently of socket lifetime. */
    private lastSeenPid: Map<string, number> = new Map();
    /** Current process-lifetime socket for each announced MCP instance. */
    private activeLifecycleSockets: Map<string, { pid: number; socket: net.Socket }> = new Map();
    private socketErrors = new WeakMap<net.Socket, NodeJS.ErrnoException>();
    private requestMethods = new WeakMap<net.Socket, string>();

    /** Fires when an MCP subprocess announces its startup via mcpStarted IPC. */
    private readonly _onMcpSubprocessStarted = new vscode.EventEmitter<{
        instanceId: string;
        pid: number;
    }>();
    /** Subscribe to receive the accepted generation's process identity. */
    public readonly onMcpSubprocessStarted = this._onMcpSubprocessStarted.event;

    /** Fires when an MCP subprocess TCP connection closes (subprocess stopped, crashed, or hard-killed). */
    private readonly _onMcpSubprocessStopped = new vscode.EventEmitter<{
        instanceId: string;
        pid: number;
        cause: string;
        level: 'info' | 'warn';
    }>();
    /**
     * Subscribe to receive (instanceId, pid) when an MCP subprocess stops.
     * Reports lifecycle connection loss, not confirmed OS process exit.
     */
    public readonly onMcpSubprocessStopped = this._onMcpSubprocessStopped.event;

    constructor(
        private readonly logger: Logger,
        getSigilSecret?: () => string,
        private readonly approvalLogger: Logger = logger,
    ) {
        const config = vscode.workspace.getConfiguration('ck3lens');
        // Use 0 = OS-assigned dynamic port (unique per window).
        // Config override exists for debugging only.
        this.port = config.get<number>('ipcPort') || 0;
        this.getSigilSecret = getSigilSecret;
    }

    /**
     * Set the Sigil secret getter (can be called after construction)
     */
    public setSigilSecret(getter: () => string): void {
        this.getSigilSecret = getter;
    }

    /**
     * Set the instance ID for per-window port file isolation.
     * Must be called before start() so that writePortFile uses the correct path.
     */
    public setInstanceId(id: string): void {
        if (this.instanceId && this.instanceId !== id) {
            if (this.lastSeenPid.has(this.instanceId)) {
                throw new Error('MCP process cleanup must complete before IPC rebinding');
            }
            this.instanceId = id;
            if (this.server?.listening) {
                this.writePortFile(this.getPort());
            }
            this.acceptingStarts = true;
            return;
        }
        this.instanceId = id;
        this.acceptingStarts = true;
    }

    public withdrawInstance(): void {
        this.acceptingStarts = false;
        this.ownerToken = undefined;
    }

    public expectOwnedProcess(token: string): void {
        this.ownerToken = token;
    }

    /**
     * Start the TCP server
     */
    public start(): Promise<void> {
        if (this.starting) {
            return this.starting;
        }
        if (this.isAvailable()) {
            return Promise.resolve();
        }
        const server = net.createServer(socket => this.handleConnection(socket));
        this.server = server;
        const operation = new Promise<void>((resolve, reject) => {
            let retried = false;
            let settled = false;
            const fail = (error: unknown): void => {
                this.logger.error('Diagnostics IPC startup failed', error);
                settled = true;
                if (server.listening) {
                    server.close();
                }
                this.server = null;
                reject(error);
            };
            server.on('listening', () => {
                try {
                    const address = server.address();
                    if (!address || typeof address === 'string') {
                        throw new Error('Diagnostics IPC listener has no TCP address');
                    }
                    this.writePortFile(address.port);
                    this.logger.info(`Diagnostics IPC server listening on ${this.host}:${address.port}`);
                    settled = true;
                    resolve();
                } catch (error) {
                    fail(error);
                }
            });
            server.on('error', (err: NodeJS.ErrnoException) => {
                if (settled) {
                    this.logger.error('Diagnostics IPC listener error', err);
                } else if (err.code === 'EADDRINUSE' && !retried && this.port !== 0) {
                    retried = true;
                    this.logger.info(`IPC port ${this.port} in use, trying next port...`);
                    try {
                        server.listen(this.port + 1, this.host);
                    } catch (error) {
                        fail(error);
                    }
                } else {
                    fail(err);
                }
            });
            try {
                server.listen(this.port, this.host);
            } catch (error) {
                fail(error);
            }
        });
        this.starting = operation.finally(() => { this.starting = undefined; });
        return this.starting;
    }

    private isProcessAlive(pid: unknown): boolean {
        if (!Number.isInteger(pid) || Number(pid) <= 0) {
            return false;
        }
        try {
            process.kill(Number(pid), 0);
            return true;
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code === 'ESRCH') {
                return false;
            }
            throw error;
        }
    }

    private deleteModeFile(instanceId: string): void {
        const fs = require('fs');
        const path = require('path');
        const modeFile = getAgentModeFilePath(instanceId);
        try {
            if (fs.existsSync(modeFile)) {
                fs.unlinkSync(modeFile);
                this.logger.debug(`Deleted retired mode file: ${path.basename(modeFile)}`);
            }
        } catch (err) {
            this.logger.error(`Failed to delete mode file for ${instanceId}`, err);
            throw err;
        }
    }

    /**
     * Check if IPC is available
     */
    public isAvailable(): boolean {
        return this.server?.listening === true;
    }

    /**
     * Write port number to a file so MCP server can discover it.
     * Uses instance-scoped filename (ipc_port_{instanceId}) to avoid cross-window collisions.
     */
    private writePortFile(port: number): void {
        const fs = require('fs');
        const portFile = getIpcPortFilePath(this.instanceId);
        
        try {
            // Ensure directory exists
            if (!fs.existsSync(CK3RAVEN_DATA_ROOT)) {
                fs.mkdirSync(CK3RAVEN_DATA_ROOT, { recursive: true });
            }
            fs.writeFileSync(portFile, JSON.stringify({
                port,
                pid: process.pid,
                instanceId: this.instanceId,
                timestamp: Date.now()
            }));
            this.logger.debug(
                `Published IPC binding: instanceId=${this.instanceId ?? 'unset'} `
                + `port=${port} extensionHostPid=${process.pid} file=${portFile}`
            );
        } catch (err) {
            this.logger.error('Failed to write port file', err);
            throw err;
        }
    }

    /**
     * Delete port file on shutdown
     */
    private deletePortFile(instanceId: string): void {
        const fs = require('fs');
        const portFile = getIpcPortFilePath(instanceId);
        try {
            if (fs.existsSync(portFile)) {
                fs.unlinkSync(portFile);
                this.logger.debug(`Deleted IPC port file: ${portFile}`);
            }
        } catch (err) {
            this.logger.error(`Failed to delete port file for ${instanceId}`, err);
            throw err;
        }
    }

    /**
     * Handle a new client connection
     */
    private handleConnection(socket: net.Socket): void {
        this.clients.add(socket);
        this.logger.debug(`IPC client connected from ${socket.remoteAddress}`);

        let buffer = '';

        socket.on('data', async (data) => {
            buffer += data.toString();
            
            // Process complete JSON-RPC messages (newline-delimited)
            let newlineIndex: number;
            while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
                const line = buffer.slice(0, newlineIndex);
                buffer = buffer.slice(newlineIndex + 1);
                
                if (line.trim()) {
                    try {
                        const request: JsonRpcRequest = JSON.parse(line);
                        const response = await this.handleRequest(request, socket);
                        socket.write(JSON.stringify(response) + '\n');
                    } catch (err) {
                        const errorResponse: JsonRpcResponse = {
                            jsonrpc: '2.0',
                            id: 0,
                            error: {
                                code: -32700,
                                message: 'Parse error',
                                data: String(err)
                            }
                        };
                        socket.write(JSON.stringify(errorResponse) + '\n');
                    }
                }
            }
        });

        socket.on('close', () => {
            this.clients.delete(socket);
            this.logger.debug('IPC client disconnected');
        });

        socket.on('error', (err) => {
            this.socketErrors.set(socket, err);
            const lifecycleEntry = Array.from(this.activeLifecycleSockets.entries())
                .find(([, active]) => active.socket === socket);
            if (!lifecycleEntry) {
                this.logger.error(
                    `IPC request socket error: peer=${socket.remoteAddress ?? 'unknown'}:${socket.remotePort ?? 'unknown'}` +
                    ` method=${this.requestMethods.get(socket) ?? 'no request received'}`,
                    err,
                );
            }
            this.clients.delete(socket);
        });
    }

    /**
     * Handle a JSON-RPC request
     */
    private async handleRequest(request: JsonRpcRequest, socket?: net.Socket): Promise<JsonRpcResponse> {
        const { id, method, params } = request;
        if (socket) {
            this.requestMethods.set(socket, method);
        }

        try {
            let result: any;

            switch (method) {
                case 'ping':
                    result = { status: 'ok', timestamp: Date.now() };
                    break;

                case 'getDiagnostics':
                    result = await this.getDiagnostics(params);
                    break;

                case 'getAllDiagnostics':
                    result = await this.getAllDiagnostics(params);
                    break;

                case 'getWorkspaceErrors':
                    result = await this.getWorkspaceErrors(params);
                    break;

                case 'validateFile':
                    result = await this.validateFile(params);
                    break;

                case 'getOpenFiles':
                    result = this.getOpenFiles();
                    break;

                case 'getActiveFile':
                    result = this.getActiveFile();
                    break;

                case 'executeCommand':
                    result = await this.executeCommand(params);
                    break;

                case 'openScriptForReview':
                    result = await this.openScriptForReview(params);
                    break;

                case 'getScriptSignApproval':
                    result = this.getScriptSignApproval(params);
                    break;

                case 'cancelScriptSign':
                    result = this.cancelScriptSign(params);
                    break;

                case 'mcpStarted':
                    result = this.handleMcpStarted(params, socket);
                    break;

                default:
                    return {
                        jsonrpc: '2.0',
                        id,
                        error: {
                            code: -32601,
                            message: `Method not found: ${method}`
                        }
                    };
            }

            return { jsonrpc: '2.0', id, result };
        } catch (err) {
            return {
                jsonrpc: '2.0',
                id,
                error: {
                    code: -32603,
                    message: String(err)
                }
            };
        }
    }

    /**
     * Get diagnostics for a specific file
     */
    private async getDiagnostics(params: { uri?: string; path?: string }): Promise<any> {
        let uri: vscode.Uri;
        
        if (params.uri) {
            uri = vscode.Uri.parse(params.uri);
        } else if (params.path) {
            uri = vscode.Uri.file(params.path);
        } else {
            throw new Error('Either uri or path required');
        }

        const diagnostics = vscode.languages.getDiagnostics(uri);
        
        return {
            uri: uri.toString(),
            path: uri.fsPath,
            diagnostics: diagnostics.map(d => this.serializeDiagnostic(d))
        };
    }

    /**
     * Get all diagnostics across all files
     */
    private async getAllDiagnostics(params?: { 
        severity?: 'error' | 'warning' | 'info' | 'hint';
        source?: string;
        limit?: number;
    }): Promise<any> {
        const allDiagnostics = vscode.languages.getDiagnostics();
        
        let results: Array<{
            uri: string;
            path: string;
            diagnostics: any[];
        }> = [];

        for (const [uri, diagnostics] of allDiagnostics) {
            let filtered = diagnostics;

            // Filter by severity
            if (params?.severity) {
                const severityMap: Record<string, vscode.DiagnosticSeverity> = {
                    'error': vscode.DiagnosticSeverity.Error,
                    'warning': vscode.DiagnosticSeverity.Warning,
                    'info': vscode.DiagnosticSeverity.Information,
                    'hint': vscode.DiagnosticSeverity.Hint
                };
                const targetSeverity = severityMap[params.severity];
                if (targetSeverity !== undefined) {
                    filtered = filtered.filter(d => d.severity === targetSeverity);
                }
            }

            // Filter by source
            if (params?.source) {
                filtered = filtered.filter(d => d.source === params.source);
            }

            if (filtered.length > 0) {
                results.push({
                    uri: uri.toString(),
                    path: uri.fsPath,
                    diagnostics: filtered.map(d => this.serializeDiagnostic(d))
                });
            }
        }

        // Apply limit
        if (params?.limit) {
            results = results.slice(0, params.limit);
        }

        return {
            fileCount: results.length,
            files: results,
            totalDiagnostics: results.reduce((sum, f) => sum + f.diagnostics.length, 0)
        };
    }

    /**
     * Get workspace-wide error summary
     */
    private async getWorkspaceErrors(params?: {
        includeWarnings?: boolean;
        groupBy?: 'file' | 'source' | 'severity';
    }): Promise<any> {
        const allDiagnostics = vscode.languages.getDiagnostics();
        
        let errorCount = 0;
        let warningCount = 0;
        let infoCount = 0;
        const bySource: Record<string, number> = {};
        const byFile: Record<string, { errors: number; warnings: number }> = {};

        for (const [uri, diagnostics] of allDiagnostics) {
            const filePath = uri.fsPath;
            byFile[filePath] = { errors: 0, warnings: 0 };

            for (const d of diagnostics) {
                const source = d.source || 'unknown';
                bySource[source] = (bySource[source] || 0) + 1;

                switch (d.severity) {
                    case vscode.DiagnosticSeverity.Error:
                        errorCount++;
                        byFile[filePath].errors++;
                        break;
                    case vscode.DiagnosticSeverity.Warning:
                        warningCount++;
                        byFile[filePath].warnings++;
                        break;
                    case vscode.DiagnosticSeverity.Information:
                    case vscode.DiagnosticSeverity.Hint:
                        infoCount++;
                        break;
                }
            }
        }

        // Get files with most errors
        const topErrorFiles = Object.entries(byFile)
            .filter(([_, counts]) => counts.errors > 0)
            .sort((a, b) => b[1].errors - a[1].errors)
            .slice(0, 10)
            .map(([path, counts]) => ({ path, ...counts }));

        return {
            summary: {
                errors: errorCount,
                warnings: warningCount,
                info: infoCount,
                filesWithErrors: Object.values(byFile).filter(f => f.errors > 0).length,
                filesWithWarnings: Object.values(byFile).filter(f => f.warnings > 0).length
            },
            bySource,
            topErrorFiles,
            sources: Object.keys(bySource)
        };
    }

    /**
     * Trigger validation for a specific file
     */
    private async validateFile(params: { path: string }): Promise<any> {
        const uri = vscode.Uri.file(params.path);
        
        // Try to open the document to trigger validation
        try {
            const document = await vscode.workspace.openTextDocument(uri);
            
            // Wait a moment for language servers to process
            await new Promise(resolve => setTimeout(resolve, 500));
            
            const diagnostics = vscode.languages.getDiagnostics(uri);
            
            return {
                uri: uri.toString(),
                path: uri.fsPath,
                languageId: document.languageId,
                diagnostics: diagnostics.map(d => this.serializeDiagnostic(d))
            };
        } catch (err) {
            throw new Error(`Failed to open file: ${params.path}`);
        }
    }

    /**
     * Get list of currently open files
     */
    private getOpenFiles(): any {
        const openDocuments = vscode.workspace.textDocuments
            .filter(d => !d.isUntitled && d.uri.scheme === 'file')
            .map(d => ({
                uri: d.uri.toString(),
                path: d.uri.fsPath,
                languageId: d.languageId,
                isDirty: d.isDirty,
                lineCount: d.lineCount
            }));

        return {
            count: openDocuments.length,
            files: openDocuments
        };
    }

    /**
     * Get the currently active file
     */
    private getActiveFile(): any {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            return { active: false };
        }

        const document = editor.document;
        const diagnostics = vscode.languages.getDiagnostics(document.uri);

        return {
            active: true,
            uri: document.uri.toString(),
            path: document.uri.fsPath,
            languageId: document.languageId,
            isDirty: document.isDirty,
            lineCount: document.lineCount,
            selection: {
                line: editor.selection.active.line + 1,
                column: editor.selection.active.character + 1
            },
            diagnostics: diagnostics.map(d => this.serializeDiagnostic(d))
        };
    }

    /**
     * Execute a VS Code command
     */
    private async executeCommand(params: { command: string; args?: any[] }): Promise<any> {
        // Whitelist of allowed commands for security
        const allowedCommands = [
            'ck3lens.refreshViews',
            'workbench.action.problems.focus',
            'editor.action.marker.next',
            'editor.action.marker.prev'
        ];

        if (!allowedCommands.includes(params.command)) {
            throw new Error(`Command not allowed: ${params.command}`);
        }

        const result = await vscode.commands.executeCommand(params.command, ...(params.args || []));
        return { executed: true, command: params.command, result };
    }

    // =========================================================================
    // Script Signing IPC Methods
    // =========================================================================

    /**
     * Open a script in VS Code with CodeLens Approve/Reject buttons.
     * Called via IPC from the MCP server when agent requests script signing.
     */
    private async openScriptForReview(params: { path: string; content_sha256: string; canonical_path: string }): Promise<any> {
        const { path: scriptPath, content_sha256, canonical_path } = params;
        if (!scriptPath || !content_sha256 || !canonical_path) {
            throw new Error('path, content_sha256, and canonical_path are required');
        }

        // Normalize path through Uri for consistent casing/separators on Windows
        const uri = vscode.Uri.file(scriptPath);
        const normalizedPath = uri.fsPath;

        // Cancel any previous pending request for this path
        this.disposeScriptSign(normalizedPath);

        // Load document without displaying it yet
        const document = await vscode.workspace.openTextDocument(uri);

        // Register commands BEFORE showing document
        this.registerScriptSignCommands();

        // Register CodeLens provider BEFORE showTextDocument.
        // VS Code queries registered CodeLens providers during the initial editor render.
        // The previous order (show first, register after) was the root cause of buttons not
        // appearing: the editor rendered with no providers registered, and subsequent
        // forceRefresh() calls may be debounced/dropped for an already-rendered editor.
        const codeLensProvider = new ScriptSignCodeLensProvider(normalizedPath, this.approvalLogger);
        const codeLensDisposable = vscode.languages.registerCodeLensProvider(
            { scheme: 'file' },
            codeLensProvider
        );

        // Store pending state BEFORE showing so state is consistent when lenses are clicked
        this.pendingScriptSigns.set(normalizedPath, {
            path: normalizedPath,
            canonical_path,    // Canonical address — used in HMAC payload, not host path
            content_sha256,
            status: 'pending',
            codeLensDisposable,
            codeLensProvider,
        });

        // NOW show the document — provider is already registered, lenses render on first paint
        await vscode.window.showTextDocument(document, { preview: false });

        // Belt-and-suspenders: also fire refresh 200ms after show in case VS Code deferred
        // the initial CodeLens query (e.g. document was already open in a background tab)
        setTimeout(() => codeLensProvider.forceRefresh(), 200);

        this.approvalLogger.info(`Script opened for review: ${normalizedPath}`);
        return { opened: true, normalizedPath };
    }

    /**
     * Check if user has approved or rejected a script.
     * Called repeatedly (polled) by the MCP server.
     */
    private getScriptSignApproval(params: { path: string }): any {
        const normalizedPath = vscode.Uri.file(params.path).fsPath;
        const pending = this.pendingScriptSigns.get(normalizedPath);
        if (!pending) {
            return { error: 'No pending request for this path' };
        }

        if (pending.status === 'pending') {
            return { pending: true };
        }

        if (pending.status === 'approved') {
            const result = {
                approved: true,
                signature: pending.signature,
                content_sha256: pending.content_sha256,
                signed_at: pending.signed_at,
            };
            // Clean up after returning approval
            this.disposeScriptSign(normalizedPath);
            return result;
        }

        if (pending.status === 'rejected') {
            // Clean up after returning rejection
            this.disposeScriptSign(normalizedPath);
            return { approved: false };
        }

        return { error: 'Unknown status' };
    }

    /**
     * Cancel a pending script signing request, disposing CodeLens.
     * Called on timeout by the MCP server.
     */
    private cancelScriptSign(params: { path: string }): any {
        const normalizedPath = vscode.Uri.file(params.path).fsPath;
        this.disposeScriptSign(normalizedPath);
        return { cancelled: true };
    }

    /**
     * Dispose a script signing request: remove CodeLens and clean up state.
     */
    private disposeScriptSign(path: string): void {
        const pending = this.pendingScriptSigns.get(path);
        if (pending) {
            pending.codeLensDisposable?.dispose();
            pending.codeLensProvider?.dispose();
            this.pendingScriptSigns.delete(path);
        }
    }

    /**
     * Register the approve/reject commands (idempotent).
     */
    private scriptSignCommandsRegistered = false;
    private registerScriptSignCommands(): void {
        // Dispose any stale registrations from a prior DiagnosticsServer instance.
        // scriptSignCommandDisposables is static so this reaches across instance boundaries,
        // handling the case where the old instance was not properly disposed before a new
        // one was created (e.g. extension reactivated without full window reload).
        for (const d of DiagnosticsServer.scriptSignCommandDisposables) {
            d.dispose();
        }
        DiagnosticsServer.scriptSignCommandDisposables = [];
        this.scriptSignCommandsRegistered = true;

        DiagnosticsServer.scriptSignCommandDisposables.push(
            vscode.commands.registerCommand('ck3lens.scriptSign.approve', (scriptPath: string) => {
                this.handleScriptApproval(scriptPath, true);
            })
        );
        DiagnosticsServer.scriptSignCommandDisposables.push(
            vscode.commands.registerCommand('ck3lens.scriptSign.reject', (scriptPath: string) => {
                this.handleScriptApproval(scriptPath, false);
            })
        );
    }

    /**
     * Handle the user clicking Approve or Reject in CodeLens.
     */
    private handleScriptApproval(scriptPath: string, approved: boolean): void {
        const pending = this.pendingScriptSigns.get(scriptPath);
        if (!pending) {
            vscode.window.showWarningMessage('No pending script signing request for this file.');
            return;
        }

        if (approved) {
            // Sign the script payload with Sigil
            const sigilSecret = this.getSigilSecret?.();
            if (!sigilSecret) {
                vscode.window.showErrorMessage('Sigil secret not available. MCP server may not be running.');
                return;
            }

            // Sign using canonical path (session-absolute), NOT host path.
            // Host paths must never leak into signing payloads.
            const payload = `script:${pending.canonical_path}|${pending.content_sha256}`;
            const signature = crypto
                .createHmac('sha256', Buffer.from(sigilSecret, 'hex'))
                .update(payload)
                .digest('hex');

            pending.status = 'approved';
            pending.signature = signature;
            pending.signed_at = new Date().toISOString();

            // Dispose CodeLens (no longer needed)
            pending.codeLensDisposable?.dispose();
            pending.codeLensDisposable = undefined;

            this.approvalLogger.info(`Script approved: ${scriptPath}`);
            vscode.window.showInformationMessage(`Script approved: ${scriptPath.split(/[\\/]/).pop()}`);
        } else {
            pending.status = 'rejected';

            // Dispose CodeLens
            pending.codeLensDisposable?.dispose();
            pending.codeLensDisposable = undefined;

            this.approvalLogger.info(`Script rejected: ${scriptPath}`);
            vscode.window.showWarningMessage(`Script rejected: ${scriptPath.split(/[\\/]/).pop()}`);
        }
    }

    /**
     * Serialize a VS Code Diagnostic to a plain object
     */
    private serializeDiagnostic(d: vscode.Diagnostic): any {
        return {
            range: {
                start: { line: d.range.start.line + 1, character: d.range.start.character },
                end: { line: d.range.end.line + 1, character: d.range.end.character }
            },
            message: d.message,
            severity: this.severityToString(d.severity),
            source: d.source,
            code: typeof d.code === 'object' ? d.code.value : d.code,
            relatedInformation: d.relatedInformation?.map(ri => ({
                location: {
                    uri: ri.location.uri.toString(),
                    path: ri.location.uri.fsPath,
                    range: {
                        start: { line: ri.location.range.start.line + 1, character: ri.location.range.start.character },
                        end: { line: ri.location.range.end.line + 1, character: ri.location.range.end.character }
                    }
                },
                message: ri.message
            })),
            tags: d.tags?.map(t => t === vscode.DiagnosticTag.Unnecessary ? 'unnecessary' : 'deprecated')
        };
    }

    /**
     * Convert severity enum to string
     */
    private severityToString(severity: vscode.DiagnosticSeverity): string {
        switch (severity) {
            case vscode.DiagnosticSeverity.Error: return 'error';
            case vscode.DiagnosticSeverity.Warning: return 'warning';
            case vscode.DiagnosticSeverity.Information: return 'info';
            case vscode.DiagnosticSeverity.Hint: return 'hint';
            default: return 'unknown';
        }
    }

    /**
     * Retire the process by closing its owner-window lifecycle lease, then verify
     * OS process exit. Direct PID termination is only a short fallback when the
     * process does not honor lease EOF.
     */
    public async ensureMcpSubprocessStopped(
        instanceId: string,
        graceMs: number = 1000,
        terminateWaitMs: number = 1000
    ): Promise<{ pid: number | null; forced: boolean; stopped: boolean }> {
        const expected = this.activeLifecycleSockets.get(instanceId);
        const pid = this.lastSeenPid.get(instanceId);
        if (pid === undefined) {
            // No process has announced this generation; there is no owned PID to stop.
            return { pid: null, forced: false, stopped: true };
        }

        if (expected) {
            this.logger.debug(`Closing lifecycle lease for MCP subprocess ${pid}`);
            expected.socket.destroy();
        }
        if (await this.waitForProcessExit(pid, graceMs)) {
            return { pid, forced: false, stopped: true };
        }
        this.logger.warn(`MCP subprocess ${pid} did not exit after ${graceMs}ms; terminating PID`);
        try {
            process.kill(pid);
        } catch (err) {
            if (!this.isProcessAlive(pid)) {
                return { pid, forced: true, stopped: true };
            }
            this.logger.error(`Failed to terminate MCP subprocess ${pid}`, err);
            return { pid, forced: true, stopped: false };
        }

        const stopped = await this.waitForProcessExit(pid, terminateWaitMs);
        if (!stopped) {
            this.logger.error(`MCP subprocess ${pid} remained alive after termination`);
        }
        return { pid, forced: true, stopped };
    }

    public removeInstancePublications(instanceId: string): void {
        const pid = this.lastSeenPid.get(instanceId);
        if (pid !== undefined && this.isProcessAlive(pid)) {
            throw new Error(`Cannot remove publications of live MCP process ${pid}`);
        }
        this.deletePortFile(instanceId);
        this.deleteModeFile(instanceId);
        this.activeLifecycleSockets.delete(instanceId);
        this.lastSeenPid.delete(instanceId);
    }

    private async waitForProcessExit(pid: number, timeoutMs: number): Promise<boolean> {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            if (!this.isProcessAlive(pid)) {
                return true;
            }
            await new Promise(resolve => setTimeout(resolve, 25));
        }
        return !this.isProcessAlive(pid);
    }

    /**
     * Stop the server
     */
    public stop(): void {
        // Close all client connections
        for (const client of this.clients) {
            client.destroy();
        }
        this.clients.clear();

        // Close the server
        if (this.server) {
            this.server.close();
            this.server = null;
        }

        // Instance publications are removed by common lifecycle cleanup.
    }

    /**
     * Get the current port
     */
    public getPort(): number {
        const address = this.server?.address();
        return typeof address === 'object' ? address?.port || this.port : this.port;
    }

    /**
     * Handle the mcpStarted JSON-RPC method.
     *
     * A generation may acquire one process lease. Another hello must not
     * supersede or kill the currently owned process.
     *
     * Params: { instanceId: string, pid: number }
     */
    private handleMcpStarted(params: any, socket?: net.Socket): object {
        const instanceId: string | undefined = params?.instanceId ?? params?.instance_id;
        const pid: number | undefined = params?.pid;

        if (!instanceId || !Number.isInteger(pid) || !pid || pid <= 0 || !socket) {
            this.logger.warn(`mcpStarted: missing required lifecycle data: ${JSON.stringify(params)}`);
            return { ok: false, reason: 'missing instanceId, pid, or lifecycle socket' };
        }
        if (!this.acceptingStarts) {
            this.logger.warn(`MCP hello arrived during cleanup: instanceId=${instanceId} pid=${pid}`);
            return { ok: false, reason: 'generation is cleaning up' };
        }
        if (this.instanceId && instanceId !== this.instanceId) {
            const reason = 'startup instanceId does not match current published generation';
            this.logger.warn(
                `Rejected MCP startup announcement: announcedInstanceId=${instanceId} pid=${pid} `
                + `currentInstanceId=${this.instanceId}; ${reason}; no lifecycle lease granted`,
            );
            return { ok: false, reason };
        }
        const suppliedToken = typeof params?.ownerToken === 'string' ? Buffer.from(params.ownerToken) : Buffer.alloc(0);
        const expectedToken = this.ownerToken ? Buffer.from(this.ownerToken) : Buffer.alloc(0);
        if (!expectedToken.length || suppliedToken.length !== expectedToken.length
            || !crypto.timingSafeEqual(suppliedToken, expectedToken)) {
            this.logger.warn(`Rejected MCP startup without owner token: instanceId=${instanceId} pid=${pid}`);
            return { ok: false, reason: 'process was not launched by this extension generation' };
        }

        const previousPid = this.lastSeenPid.get(instanceId);
        const previousProcess = this.activeLifecycleSockets.get(instanceId);
        if (previousPid !== undefined) {
            if (previousProcess?.socket === socket && previousPid === pid) {
                return { ok: true, instanceId, pid };
            }
            this.logger.error(`MCP generation already owns a process: instanceId=${instanceId} pid=${previousPid} additionalPid=${pid}`);
            return { ok: false, reason: 'generation already owns a process' };
        }
        this.lastSeenPid.set(instanceId, pid);
        this.activeLifecycleSockets.set(instanceId, { pid, socket });

        socket.once('close', () => {
            const active = this.activeLifecycleSockets.get(instanceId);
            if (!active || active.pid !== pid || active.socket !== socket) {
                this.logger.debug(`mcpStopped: ignored stale socket instanceId=${instanceId} pid=${pid}`);
                return;
            }
            this.activeLifecycleSockets.delete(instanceId);
            const error = this.socketErrors.get(socket);
            this._onMcpSubprocessStopped.fire({
                instanceId,
                pid,
                cause: error?.code ?? error?.message ?? 'socket closed',
                level: error ? 'warn' : 'info',
            });
        });

        this._onMcpSubprocessStarted.fire({ instanceId, pid });

        return { ok: true, instanceId, pid };
    }

    dispose(): void {
        // Dispose script sign commands so they don't survive into the next extension activation
        for (const d of DiagnosticsServer.scriptSignCommandDisposables) {
            d.dispose();
        }
        DiagnosticsServer.scriptSignCommandDisposables = [];
        this.scriptSignCommandsRegistered = false;

        // Clean up all pending script signing requests
        for (const [, pending] of this.pendingScriptSigns) {
            pending.codeLensDisposable?.dispose();
            pending.codeLensProvider?.dispose();
        }
        this.pendingScriptSigns.clear();

        // Announced PID evidence is released only by verified publication cleanup.
        this.activeLifecycleSockets.clear();
        this._onMcpSubprocessStarted.dispose();
        this._onMcpSubprocessStopped.dispose();

        this.stop();
    }
}


/**
 * CodeLens provider that shows Approve/Reject buttons at the top of a script file.
 * One instance per file, disposed when approval/rejection completes or is cancelled.
 */
class ScriptSignCodeLensProvider implements vscode.CodeLensProvider {
    private readonly normalizedPath: string;
    private readonly _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses: vscode.Event<void> = this._onDidChangeCodeLenses.event;

    constructor(
        private readonly scriptPath: string,
        private readonly logger?: Logger
    ) {
        // Normalize via Uri for consistent casing/separators on Windows
        this.normalizedPath = vscode.Uri.file(scriptPath).fsPath.toLowerCase();
    }

    /** Fire to force VS Code to re-evaluate CodeLens for this file immediately. */
    forceRefresh(): void {
        this._onDidChangeCodeLenses.fire();
    }

    dispose(): void {
        this._onDidChangeCodeLenses.dispose();
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        const docPath = document.uri.fsPath.toLowerCase();
        // Only provide lenses for the target script file (case-insensitive on Windows)
        if (docPath !== this.normalizedPath) {
            this.logger?.debug(`[ScriptSign] CodeLens: skipping ${docPath}`);
            return [];
        }
        this.logger?.debug(`[ScriptSign] CodeLens: rendering Approve/Reject for ${docPath}`);

        const topOfFile = new vscode.Range(0, 0, 0, 0);

        const approveLens = new vscode.CodeLens(topOfFile, {
            title: '$(check) Approve Script',
            command: 'ck3lens.scriptSign.approve',
            arguments: [this.scriptPath],
            tooltip: 'Approve this script for execution (Sigil-signed)',
        });

        const rejectLens = new vscode.CodeLens(topOfFile, {
            title: '$(x) Reject Script',
            command: 'ck3lens.scriptSign.reject',
            arguments: [this.scriptPath],
            tooltip: 'Reject this script — it will not be allowed to execute',
        });

        return [approveLens, rejectLens];
    }
}
