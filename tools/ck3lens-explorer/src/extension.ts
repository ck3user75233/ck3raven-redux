/**
 * CK3 Lens Explorer - VS Code Extension
 *
 * Owns the MCP/session lifecycle, diagnostics IPC, QBuilder supervision,
 * agent and contract views, chat participation, and extension diagnostics.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { AgentViewProvider } from './views/agentView';
import { ContractsViewProvider } from './views/contractsView';
import { LensStatusBar } from './widget/statusBar';
import { Logger } from './utils/logger';
import { StructuredLogger, createStructuredLogger } from './utils/structuredLogger';
import {
    ExtensionHostMonitor,
    startExtensionHostMonitor,
} from './utils/extensionHostMonitor';
import {
    registerMcpServerProvider,
    CK3LensMcpServerProvider,
    getRootRepo,
    findCk3RavenPython,
} from './mcp/mcpServerProvider';
import { createMcpGeneration, McpLifecycle } from './mcp/mcpLifecycle';
import { McpProcess } from './mcp/mcpProcess';
import { CK3RAVEN_DATA_ROOT, getAgentModeFilePath } from './config/runtimePaths';
import { DiagnosticsServer } from './ipc/diagnosticsServer';
import { QBuilderSupervisor } from './qbuilder/qbuilderSupervisor';
import { Ck3RavenParticipant } from './chat/participant';
import { runHealthCheck } from './chat/diagnose';
import { registerDoctorCommands } from './setup/doctor';

// Global extension state
let statusBar: LensStatusBar | undefined;
let mcpServerProvider: CK3LensMcpServerProvider | undefined;
let mcpRegistration: vscode.Disposable | undefined;
let diagnosticsServer: DiagnosticsServer | undefined;
let qbuilderSupervisor: QBuilderSupervisor | undefined;
let chatParticipant: Ck3RavenParticipant | undefined;
let logger: Logger;
let structuredLogger: StructuredLogger | undefined;
let extensionHostMonitor: ExtensionHostMonitor | undefined;
let outputChannel: vscode.OutputChannel;
let mcpLifecycle: McpLifecycle | undefined;
let mcpProcess: McpProcess | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    console.log('[CK3RAVEN] A0 enter activate');
    console.log('[CK3RAVEN] A0 version', context.extension.packageJSON.version);

    // Initialize logging
    outputChannel = vscode.window.createOutputChannel('CK3 Lens');
    console.log('[CK3RAVEN] A1 after output channel');

    logger = new Logger(outputChannel);
    logger.debug('CK3 Lens Explorer activating...');
    console.log('[CK3RAVEN] A2 after logger');

    // Select the instance ID before DiagnosticsServer starts so its port file and MCP definition agree.
    const initialGeneration = createMcpGeneration();
    const earlyInstanceId = initialGeneration.instanceId;
    logger.debug(`Generated fresh MCP instance: instanceId=${earlyInstanceId}`);
    console.log(`[CK3RAVEN] A3a Generated instance ID: ${earlyInstanceId}`);

    extensionHostMonitor = startExtensionHostMonitor({
        dataRoot: CK3RAVEN_DATA_ROOT,
        instanceId: earlyInstanceId,
        extensionLogPath: context.logUri.fsPath,
        vscodeVersion: vscode.version,
    });

    // The owned Python process needs its diagnostics lease before HTTP publication.
    console.log('[CK3RAVEN] A3b DiagnosticsServer starting (before MCP)');
    const approvalOutput = vscode.window.createOutputChannel('CK3 Lens Approvals');
    const approvalLogger = new Logger(approvalOutput);
    context.subscriptions.push(approvalOutput, approvalLogger);
    diagnosticsServer = new DiagnosticsServer(logger, undefined, approvalLogger);
    diagnosticsServer.setInstanceId(earlyInstanceId);
    try {
        await diagnosticsServer.start();
    } catch (error) {
        diagnosticsServer.dispose();
        logger.error('Diagnostics server failed during activate()', error);
        throw error;
    }
    console.log('[CK3RAVEN] A3c DiagnosticsServer started');
    logger.debug(`DiagnosticsServer state after start: available=${diagnosticsServer.isAvailable()} port=${diagnosticsServer.getPort()}`);

    const rootRepo = getRootRepo();
    const pythonPath = findCk3RavenPython();
    if (!rootRepo || !pythonPath) {
        diagnosticsServer.dispose();
        throw new Error('Cannot start QBuilder without a valid ck3raven repository and Python environment');
    }
    qbuilderSupervisor = new QBuilderSupervisor(rootRepo, pythonPath, earlyInstanceId, logger);
    try {
        await qbuilderSupervisor.start();
    } catch (error) {
        diagnosticsServer.dispose();
        logger.error('QBuilder supervisor failed during activate()', error);
        throw error;
    }
    logger.debug('QBuilder supervisor is healthy; registering MCP provider');

    const ipcPort = diagnosticsServer?.isAvailable() ? diagnosticsServer.getPort() : null;
    const instanceId = earlyInstanceId;
    structuredLogger = createStructuredLogger(instanceId, outputChannel);
    console.log('[CK3RAVEN] A5 after structured logger');

    structuredLogger.debug('ext.activate', 'Extension activating', {
        version: context.extension.packageJSON.version,
        instance_id: instanceId,
        extension_host_pid: process.pid,
        extension_log_path: context.logUri.fsPath,
        ipc_port: ipcPort,
    });

    const agentProvider = new AgentViewProvider(
        context,
        logger,
        () => mcpLifecycle?.current.instanceId ?? earlyInstanceId,
        () => mcpServerProvider?.getSigilSecret() ?? '',
    );
    // Wire IPC status so agentView can display it
    agentProvider.setIpcStatusProvider(() => ({
        available: diagnosticsServer?.isAvailable() ?? false,
        port: diagnosticsServer?.getPort() ?? 0
    }));
    const diagnostics = diagnosticsServer;
    const supervisor = qbuilderSupervisor;
    const reportLifecycleFailure = (error: unknown): void => {
        structuredLogger?.error('ext.mcp', 'MCP lifecycle operation failed', {
            instance_id: mcpLifecycle?.current.instanceId,
            error: error instanceof Error ? error.message : String(error),
        });
        void vscode.window.showErrorMessage('CK3 Lens MCP cleanup/start failed. See the CK3 Lens output.');
    };
    const lifecycle = new McpLifecycle({
        publish: async (generation, signal) => {
            if (!diagnostics.isAvailable()) {
                throw new Error('Cannot publish MCP without the owning IPC listener');
            }
            diagnostics.setInstanceId(generation.instanceId);
            structuredLogger?.setInstanceId(generation.instanceId);
            const modeFile = getAgentModeFilePath(generation.instanceId);
            fs.mkdirSync(path.dirname(modeFile), { recursive: true });
            fs.writeFileSync(modeFile, JSON.stringify({ mode: null, instance_id: generation.instanceId }));
            const ownedProcess = new McpProcess(rootRepo, pythonPath, CK3RAVEN_DATA_ROOT, logger,
                (id, code, signal) => {
                    structuredLogger?.warn('ext.mcp', 'Owned MCP process exited', {
                        instance_id: id, exit_code: code, signal,
                    });
                    void lifecycle.connectionLost(id).catch(reportLifecycleFailure);
                });
            mcpProcess = ownedProcess;
            diagnostics.expectOwnedProcess(ownedProcess.getOwnerToken());
            const endpoint = await ownedProcess.start(generation, diagnostics.getPort(), 120_000, signal);
            signal.throwIfAborted();
            const result = registerMcpServerProvider(logger, generation, endpoint);
            mcpServerProvider = result.provider;
            mcpRegistration = result.registration;
            try {
                agentProvider.refresh();
            } catch (error) {
                structuredLogger?.error('ext.ui', 'AgentView refresh failed after MCP publication', {
                    instance_id: generation.instanceId,
                    error: error instanceof Error ? error.message : String(error),
                });
            }
            structuredLogger?.info('ext.mcp', 'MCP definition published; initialization required', {
                instance_id: generation.instanceId,
            });
        },
        withdraw: () => {
            diagnostics.withdrawInstance();
            mcpRegistration?.dispose();
            mcpRegistration = undefined;
            mcpServerProvider?.dispose();
            mcpServerProvider = undefined;
        },
        stopProcess: async id => {
            const owned = await mcpProcess?.stop();
            if (owned && !owned.stopped) {
                return owned;
            }
            const announced = await diagnostics.ensureMcpSubprocessStopped(id);
            if (announced.stopped) {
                mcpProcess = undefined;
            }
            return {
                pid: announced.pid ?? owned?.pid ?? null,
                forced: announced.forced || (owned?.forced ?? false),
                stopped: announced.stopped,
            };
        },
        removePublications: id => diagnostics.removeInstancePublications(id),
        releaseClient: id => supervisor.unregisterMcpClient(id),
        cleanupCompleted: (id, result) => {
            structuredLogger?.info('ext.mcp', 'MCP generation cleanup completed', {
                instance_id: id,
                pid: result.pid,
                forced: result.forced,
                process_evidence: result.pid === null ? 'no announced PID' : 'exit confirmed',
            });
        },
        disconnected: () => {
            agentProvider.onMcpDisconnected();
            statusBar?.setMcpServerStatus('disconnected');
        },
        reportError: (step, error) => {
            structuredLogger?.error('ext.mcp', `MCP lifecycle presentation failed: ${step}`, {
                instance_id: mcpLifecycle?.current.instanceId,
                error: error instanceof Error ? error.message : String(error),
            });
        },
    }, initialGeneration);
    mcpLifecycle = lifecycle;
    context.subscriptions.push(
        diagnostics.onMcpSubprocessStarted(({ instanceId: reportedId, pid }) => {
            lifecycle.connected(reportedId);
            statusBar?.setMcpServerStatus('connected');
            agentProvider.setMcpServerStatus('connected', reportedId);
            structuredLogger?.info(
                'ext.mcp',
                'MCP lifecycle handshake completed',
                {
                    pid,
                    instance_id: reportedId,
                }
            );
        })
    );

    context.subscriptions.push(
        diagnostics.onMcpSubprocessStopped(({ instanceId: stoppedId, pid, cause, level }) => {
            structuredLogger?.[level]('ext.mcp', 'MCP lifecycle disconnected', {
                instance_id: stoppedId,
                pid,
                cause,
            });
            void lifecycle.connectionLost(stoppedId).catch(reportLifecycleFailure);
        })
    );
    diagnostics.setSigilSecret(() => lifecycle.current.sigilSecret);
    try {
        await lifecycle.start();
    } catch (error) {
        reportLifecycleFailure(error);
        await deactivate();
        throw error;
    }

    // ContractsView: Shows active contracts and operation history
    const contractsProvider = new ContractsViewProvider(logger);
    console.log('[CK3RAVEN] A9 after view providers created');

    context.subscriptions.push(
        agentProvider,
        vscode.window.registerTreeDataProvider('ck3lens.agentView', agentProvider),
        vscode.window.registerTreeDataProvider('ck3lens.contractsView', contractsProvider),
    );

    // Initialize the Status Bar
    statusBar = new LensStatusBar(context, logger);
    context.subscriptions.push(statusBar);

    // Register commands
    console.log('[CK3RAVEN] A13 before registerCommands');
    registerCommands(context, agentProvider, contractsProvider);
    console.log('[CK3RAVEN] A14 after registerCommands');

    // ========================================================================
    // CK3 Raven Chat Participant (V1 Brief)
    // ========================================================================
    console.log('[CK3RAVEN] A11 Chat Participant starting');

    // Check if Chat API exists
    if (typeof vscode.chat?.createChatParticipant !== 'function') {
        console.log('[CK3RAVEN] A11a Chat API not available');
        logger.info('Chat Participant API not available - skipping @ck3raven registration');
    } else {
        console.log('[CK3RAVEN] A11b Chat API available, creating participant');
        chatParticipant = new Ck3RavenParticipant(context, logger);
        console.log('[CK3RAVEN] A11c participant created');
        context.subscriptions.push(chatParticipant);

        logger.debug('CK3 Raven Chat Participant registered');

        // Health check command
        context.subscriptions.push(
            vscode.commands.registerCommand('ck3raven.chat.health', async () => {
                const health = await runHealthCheck(context.extension.packageJSON.version);

                // Show in information message
                vscode.window.showInformationMessage(
                    health.mcp_tools_registered
                        ? `CK3 Raven: ${health.mcp_tool_count} tools registered`
                        : 'CK3 Raven: No tools registered - check MCP connection'
                );
            })
        );

        logger.debug('CK3 Raven Chat Participant registered');
    }
    console.log('[CK3RAVEN] A12 after chat participant');

    // ========================================================================
    // Doctor Commands (Dev Host Determinism Shim)
    // ========================================================================
    console.log('[CK3RAVEN] A19 before registerDoctorCommands');
    registerDoctorCommands(
        context,
        () => mcpServerProvider?.getInstanceId(),
        logger,
        outputChannel
    );
    console.log('[CK3RAVEN] A20 after registerDoctorCommands');
    logger.debug('Doctor commands registered');

    logger.info('CK3 Lens Explorer activated successfully');
    console.log('[CK3RAVEN] A23 ACTIVATE COMPLETE - returning from activate()');
}

/**
 * Register retained extension commands.
 */
function registerCommands(
    context: vscode.ExtensionContext,
    agentProvider: AgentViewProvider,
    contractsProvider: ContractsViewProvider
): void {
    // Refresh retained views
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3lens.refreshViews', async () => {
            await agentProvider.recheckMcpStatus();
            contractsProvider.refresh();
        })
    );

    // Show operation details in output channel
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3lens.contracts.showOperationDetails', async (entry: unknown) => {
            const outputChannel = vscode.window.createOutputChannel('CK3 Lens Operation Details');
            outputChannel.clear();
            outputChannel.appendLine('='.repeat(60));
            outputChannel.appendLine('OPERATION DETAILS');
            outputChannel.appendLine('='.repeat(60));
            outputChannel.appendLine('');
            outputChannel.appendLine(JSON.stringify(entry, null, 2));
            outputChannel.show();
        })
    );

    // View bug reports
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3lens.contracts.viewBugReports', async () => {
            const bugReportsPath = path.join(CK3RAVEN_DATA_ROOT, 'bug_reports');

            if (!fs.existsSync(bugReportsPath)) {
                vscode.window.showInformationMessage('No bug reports found.');
                return;
            }

            // Open the bug reports folder
            const uri = vscode.Uri.file(bugReportsPath);
            await vscode.commands.executeCommand('revealFileInOS', uri);
        })
    );

    // Refresh contracts view
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3lens.contracts.refresh', () => {
            contractsProvider.refresh();
        })
    );

    // File a bug report (opens bug report form)
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3lens.contracts.fileBugReport', async () => {
            // Show bug report input form
            const summary = await vscode.window.showInputBox({
                title: 'Bug Report Summary',
                prompt: 'Brief description of the issue',
                placeHolder: 'e.g., Parser fails on nested lists'
            });

            if (!summary) { return; }

            const category = await vscode.window.showQuickPick([
                { label: 'Parser', description: 'CK3 script parsing issues', value: 'parser' },
                { label: 'MCP Tools', description: 'Tool behavior issues', value: 'mcp' },
                { label: 'Extension', description: 'VS Code extension issues', value: 'extension' },
                { label: 'Database', description: 'Database/indexing issues', value: 'database' },
                { label: 'Other', description: 'Other issues', value: 'other' }
            ], {
                title: 'Bug Category',
                placeHolder: 'Select the area affected'
            });

            if (!category) { return; }

            const details = await vscode.window.showInputBox({
                title: 'Additional Details',
                prompt: 'Steps to reproduce, error messages, etc.',
                placeHolder: 'Optional additional context'
            });

            // Create bug report
            const bugReport = {
                id: `bug-${Date.now()}`,
                summary,
                category: category.value,
                details: details || '',
                timestamp: new Date().toISOString(),
                source: 'ck3lens-extension'
            };

            // Save to bug_reports folder
            const bugReportsPath = path.join(CK3RAVEN_DATA_ROOT, 'bug_reports');

            if (!fs.existsSync(bugReportsPath)) {
                fs.mkdirSync(bugReportsPath, { recursive: true });
            }

            const reportPath = path.join(bugReportsPath, `${bugReport.id}.json`);
            fs.writeFileSync(reportPath, JSON.stringify(bugReport, null, 2));

            vscode.window.showInformationMessage(`Bug report filed: ${bugReport.id}`);
            contractsProvider.refresh();
        })
    );
}

/**
 * Extension deactivation
 *
 * Common lifecycle cleanup withdraws HTTP publication, stops the owned child,
 * verifies Python exit, releases its MCP lease and removes generation publications.
 */
export async function deactivate(): Promise<void> {
    extensionHostMonitor?.beginShutdown();
    // Log deactivation start with structured logger (CANONICAL per docs/CANONICAL_LOGS.md)
    structuredLogger?.info('ext.deactivate', 'Extension deactivating');

    let cleanupSucceeded = true;
    try {
        await mcpLifecycle?.close();
    } catch (error) {
        cleanupSucceeded = false;
        structuredLogger?.error('ext.mcp', 'MCP cleanup failed during deactivation', {
            error: error instanceof Error ? error.message : String(error),
        });
    }
    mcpLifecycle = undefined;

    // Step 3: Release this window's QBuilder leases after its MCP process is gone.
    try {
        await qbuilderSupervisor?.stop();
    } catch (e) {
        cleanupSucceeded = false;
        structuredLogger?.error('ext.qbuilder', 'Error stopping QBuilder supervisor', {
            error: (e as Error).message,
        });
    }
    qbuilderSupervisor = undefined;

    // Provider registration retirement belongs exclusively to common cleanup.
    structuredLogger?.debug('ext.ipc', 'Disposing diagnostics server after MCP provider teardown');
    diagnosticsServer?.dispose();
    diagnosticsServer = undefined;

    // Step 6: Dispose remaining extension resources
    // Dispose chat participant
    chatParticipant?.dispose();
    chatParticipant = undefined;

    statusBar?.dispose();

    // Log deactivation complete BEFORE disposing loggers
    structuredLogger?.[cleanupSucceeded ? 'info' : 'error'](
        'ext.deactivate',
        cleanupSucceeded ? 'Extension deactivation complete' : 'Extension deactivation finished with cleanup errors',
    );

    // Dispose loggers last (flushes remaining entries)
    // CRITICAL: Logger must be disposed to prevent listener leaks
    logger?.dispose();
    structuredLogger?.dispose();
    structuredLogger = undefined;

    if (cleanupSucceeded) {
        extensionHostMonitor?.markCleanShutdown();
    }
    extensionHostMonitor = undefined;
}
