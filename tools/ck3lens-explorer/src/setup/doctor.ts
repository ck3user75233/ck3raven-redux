/**
 * CK3 Raven Doctor - Development Host Determinism Shim
 *
 * Provides diagnostic information for debugging extension environment issues.
 * Makes Extension Development Host testing reliable by exposing exactly which
 * paths and interpreters are being used.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { Logger } from '../utils/logger';
import {
    CK3RAVEN_DATABASE_PATH,
    CK3RAVEN_DATA_ROOT,
    CK3RAVEN_PYTHON,
    ROOT_GAME,
    ROOT_REPO,
    ROOT_STEAM,
} from '../config/runtimePaths';

/**
 * Doctor diagnostic result
 */
export interface DoctorResult {
    /** Extension mode: 'dev-host' or 'installed' */
    extensionMode: 'dev-host' | 'installed';
    
    /** Extension version */
    extensionVersion: string;
    
    /** Workspace root path */
    workspaceRoot: string | null;
    
    /** ck3raven repo root (for MCP cwd) */
    repoRoot: string | null;
    
    /** Python path configuration */
    python: {
        /** Configured path (from setting) */
        configuredPath: string | null;
        /** Resolved path (what will actually be used) */
        resolvedPath: string | null;
        /** How the path was determined */
        source: 'canonical' | 'not-found';
        /** Whether the resolved path exists */
        exists: boolean;
        /** Python version (if detectable) */
        version?: string;
    };
    
    /** Virtual environment detection */
    venv: {
        detected: boolean;
        path: string | null;
        pythonPath: string | null;
    };
    
    /** MCP server configuration */
    mcp: {
        /** Command line that would be used */
        commandLine: string[];
        /** Working directory */
        cwd: string | null;
        /** Instance ID */
        instanceId: string | null;
        /** Whether tools are registered */
        toolsRegistered: boolean;
        /** Count of ck3raven tools */
        toolCount: number;
    };
    
    /** Key paths */
    paths: {
        ck3ravenPath: string | null;
        databasePath: string | null;
        vanillaPath: string | null;
        workshopPath: string | null;
        userHome: string;
        ck3ravenHome: string;
    };
    
    /** Issues detected */
    issues: DoctorIssue[];
    
    /** Timestamp */
    timestamp: string;
}

export interface DoctorIssue {
    severity: 'error' | 'warning' | 'info';
    code: string;
    message: string;
    suggestion?: string;
}

/**
 * Run the doctor diagnostic
 */
export async function runDoctor(
    context: vscode.ExtensionContext,
    mcpInstanceId: string | undefined,
    logger: Logger
): Promise<DoctorResult> {
    const issues: DoctorIssue[] = [];
    
    // Extension mode detection
    const extensionMode = detectExtensionMode(context);
    
    // Workspace root
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
    if (!workspaceRoot) {
        issues.push({
            severity: 'error',
            code: 'NO_WORKSPACE',
            message: 'No workspace folder open',
            suggestion: 'Open a folder or workspace'
        });
    }
    
    const repoRoot = ROOT_REPO ?? null;
    const pythonPath = CK3RAVEN_PYTHON ?? null;
    const pythonExists = pythonPath !== null && fs.existsSync(pythonPath);
    const pythonResult: DoctorResult['python'] = {
        configuredPath: pythonPath,
        resolvedPath: pythonPath,
        source: pythonPath ? 'canonical' : 'not-found',
        exists: pythonExists,
    };

    const venvPath = repoRoot ? path.join(repoRoot, '.venv') : null;
    const venvResult: DoctorResult['venv'] = {
        detected: venvPath !== null && fs.existsSync(venvPath),
        path: venvPath,
        pythonPath,
    };

    const mcpResult = getMcpConfig(pythonPath, repoRoot, mcpInstanceId);
    const paths = {
        ck3ravenPath: repoRoot,
        databasePath: CK3RAVEN_DATABASE_PATH,
        vanillaPath: ROOT_GAME ?? null,
        workshopPath: ROOT_STEAM ?? null,
        userHome: path.dirname(CK3RAVEN_DATA_ROOT),
        ck3ravenHome: CK3RAVEN_DATA_ROOT,
    };

    if (!repoRoot) {
        issues.push({
            severity: 'error',
            code: 'ROOT_REPO_NOT_CONFIGURED',
            message: 'root_repo is not configured',
            suggestion: 'Set root_repo in ~/.ck3raven/config/workspace.toml'
        });
    }

    if (!pythonExists) {
        issues.push({
            severity: 'error',
            code: 'PYTHON_NOT_FOUND',
            message: `CK3Raven virtual environment is unavailable: ${pythonPath ?? 'root_repo is not configured'}`,
            suggestion: 'Create the repository .venv with the supported interpreter'
        });
    }
    
    if (mcpResult.toolCount === 0) {
        issues.push({
            severity: 'warning',
            code: 'NO_MCP_TOOLS',
            message: 'No MCP tools registered',
            suggestion: 'Check MCP server is running and reload window'
        });
    }
    
    return {
        extensionMode,
        extensionVersion: context.extension.packageJSON.version,
        workspaceRoot,
        repoRoot,
        python: pythonResult,
        venv: venvResult,
        mcp: mcpResult,
        paths,
        issues,
        timestamp: new Date().toISOString()
    };
}

/**
 * Detect if running in Extension Development Host
 */
function detectExtensionMode(context: vscode.ExtensionContext): 'dev-host' | 'installed' {
    // In dev host, extension path contains the source repo
    // In installed, it's in ~/.vscode/extensions/
    const extPath = context.extensionPath;
    
    // Check for typical dev host indicators
    if (extPath.includes('ck3raven') && !extPath.includes('.vscode/extensions')) {
        return 'dev-host';
    }
    
    // Also check if running from out/ directory (esbuild output)
    if (fs.existsSync(path.join(extPath, 'src'))) {
        return 'dev-host';
    }
    
    return 'installed';
}

/**
 * Get MCP server configuration
 */
function getMcpConfig(
    pythonPath: string | null,
    repoRoot: string | null,
    instanceId: string | undefined
): DoctorResult['mcp'] {
    // Build the command line that would be used
    const commandLine: string[] = [];
    if (pythonPath) {
        commandLine.push(pythonPath);
        commandLine.push('-m', 'tools.ck3lens_mcp.server');
    }
    
    // Get registered tools
    const allTools = vscode.lm.tools;
    const ck3Tools = allTools.filter(t => 
        t.name.includes('ck3_') || t.name.includes('ck3lens')
    );
    
    return {
        commandLine,
        cwd: repoRoot,
        instanceId: instanceId ?? null,
        toolsRegistered: ck3Tools.length > 0,
        toolCount: ck3Tools.length
    };
}

/**
 * Format doctor result for display
 */
export function formatDoctorResult(result: DoctorResult): string {
    const lines: string[] = [];
    
    lines.push('# CK3 Raven Doctor Report');
    lines.push('');
    lines.push(`**Generated:** ${result.timestamp}`);
    lines.push(`**Extension Version:** ${result.extensionVersion}`);
    lines.push(`**Extension Mode:** ${result.extensionMode}`);
    lines.push('');
    
    // Paths
    lines.push('## Paths');
    lines.push('');
    lines.push(`| Path | Value |`);
    lines.push(`|------|-------|`);
    lines.push(`| Workspace Root | \`${result.workspaceRoot ?? 'NOT SET'}\` |`);
    lines.push(`| Repo Root (MCP cwd) | \`${result.repoRoot ?? 'NOT SET'}\` |`);
    lines.push(`| ck3ravenPath | \`${result.paths.ck3ravenPath ?? 'NOT SET'}\` |`);
    lines.push(`| Database | \`${result.paths.databasePath}\` |`);
    lines.push(`| ~/.ck3raven | \`${result.paths.ck3ravenHome}\` |`);
    lines.push('');
    
    // Python
    lines.push('## Python Environment');
    lines.push('');
    lines.push(`| Property | Value |`);
    lines.push(`|----------|-------|`);
    lines.push(`| Configured Path | \`${result.python.configuredPath ?? 'NOT SET'}\` |`);
    lines.push(`| Resolved Path | \`${result.python.resolvedPath ?? 'NONE'}\` |`);
    lines.push(`| Source | ${result.python.source} |`);
    lines.push(`| Exists | ${result.python.exists ? '✅' : '❌'} |`);
    lines.push('');
    
    // Venv
    lines.push('## Virtual Environment');
    lines.push('');
    if (result.venv.detected) {
        lines.push(`✅ Detected at: \`${result.venv.path}\``);
        lines.push(`   Python: \`${result.venv.pythonPath ?? 'NOT FOUND'}\``);
    } else {
        lines.push('❌ No venv detected in repo root');
    }
    lines.push('');
    
    // MCP
    lines.push('## MCP Server');
    lines.push('');
    lines.push(`| Property | Value |`);
    lines.push(`|----------|-------|`);
    lines.push(`| Instance ID | \`${result.mcp.instanceId ?? 'NOT SET'}\` |`);
    lines.push(`| Tools Registered | ${result.mcp.toolsRegistered ? '✅' : '❌'} (${result.mcp.toolCount}) |`);
    lines.push(`| Working Directory | \`${result.mcp.cwd ?? 'NOT SET'}\` |`);
    lines.push(`| Command Line | \`${result.mcp.commandLine.join(' ') || 'NOT CONFIGURED'}\` |`);
    lines.push('');
    
    // Issues
    if (result.issues.length > 0) {
        lines.push('## Issues');
        lines.push('');
        for (const issue of result.issues) {
            const icon = issue.severity === 'error' ? '🔴' : 
                         issue.severity === 'warning' ? '🟡' : '🔵';
            lines.push(`${icon} **[${issue.code}]** ${issue.message}`);
            if (issue.suggestion) {
                lines.push(`   → ${issue.suggestion}`);
            }
        }
    } else {
        lines.push('## Status');
        lines.push('');
        lines.push('✅ No issues detected');
    }
    
    return lines.join('\n');
}

/**
 * Show doctor result in output channel and optionally in markdown preview
 */
export async function showDoctorResult(
    result: DoctorResult,
    outputChannel: vscode.OutputChannel,
    showPreview: boolean = true
): Promise<void> {
    const formatted = formatDoctorResult(result);
    
    // Always write to output channel
    outputChannel.clear();
    outputChannel.appendLine(formatted);
    outputChannel.show();
    
    // Optionally show in markdown preview
    if (showPreview) {
        const doc = await vscode.workspace.openTextDocument({
            content: formatted,
            language: 'markdown'
        });
        await vscode.window.showTextDocument(doc, { preview: true });
    }
}

/**
 * Register doctor commands
 */
export function registerDoctorCommands(
    context: vscode.ExtensionContext,
    getMcpInstanceId: () => string | undefined,
    logger: Logger,
    outputChannel: vscode.OutputChannel
): void {
    // Main doctor command
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3raven.doctor', async () => {
            const result = await runDoctor(context, getMcpInstanceId(), logger);
            await showDoctorResult(result, outputChannel);
            
            // Log to structured logger too
            logger.info('Doctor report generated');
        })
    );
    
    // Quick status check (less verbose than full doctor)
    context.subscriptions.push(
        vscode.commands.registerCommand('ck3raven.checkStatus', async () => {
            const result = await runDoctor(context, getMcpInstanceId(), logger);
            
            if (result.issues.length === 0) {
                vscode.window.showInformationMessage(
                    `✅ CK3 Raven: Ready (${result.mcp.toolCount} tools, ${result.extensionMode})`
                );
            } else {
                const errors = result.issues.filter(i => i.severity === 'error');
                const warnings = result.issues.filter(i => i.severity === 'warning');
                
                if (errors.length > 0) {
                    vscode.window.showErrorMessage(
                        `CK3 Raven: ${errors[0].message}`,
                        'Run Doctor'
                    ).then(choice => {
                        if (choice === 'Run Doctor') {
                            vscode.commands.executeCommand('ck3raven.doctor');
                        }
                    });
                } else if (warnings.length > 0) {
                    vscode.window.showWarningMessage(
                        `CK3 Raven: ${warnings[0].message}`,
                        'Run Doctor'
                    ).then(choice => {
                        if (choice === 'Run Doctor') {
                            vscode.commands.executeCommand('ck3raven.doctor');
                        }
                    });
                }
            }
        })
    );
}
