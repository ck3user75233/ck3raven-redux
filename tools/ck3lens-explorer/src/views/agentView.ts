/**
 * Agent View Provider - Sidebar view for agent status and initialization
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { CK3RAVEN_DATA_ROOT, getAgentModeFileName, getAgentModeFilePath } from '../config/runtimePaths';
import { Logger } from '../utils/logger';

export type LensMode = 'ck3lens' | 'ck3raven-dev' | 'none' | 'initializing';
export type ConnectionStatus = 'connected' | 'disconnected';

export interface AgentInstance {
    id: string;
    mode: LensMode;
    initializedAt: string;
    label: string;
    isLocal?: boolean;
    lastActivity?: string;
    lastActivityTime?: string;
}

export interface AgentState {
    mcpServer: { status: ConnectionStatus; serverName?: string };
    agents: AgentInstance[];
    session: { id: string; startedAt: string };
}

/**
 * Mode definitions with behavioral contracts
 */
export const MODE_DEFINITIONS: Record<string, {
    displayName: string;
    shortName: string;
    description: string;
    icon: string;
    initPrompt: string;
}> = {
    'ck3lens': {
        displayName: 'CK3 Lens',
        shortName: 'Lens',
        description: 'Full CK3 modding - conflict detection, local mod editing',
        icon: 'merge',
        initPrompt: `Call mcp_ck3lens-{first4}-_ck3_get_mode_instructions(mode="{mode}"{hatParam}).`
    },
    'ck3raven-dev': {
        displayName: 'CK3 Raven Dev',
        shortName: 'Raven',
        description: 'Infrastructure development - Python, MCP server',
        icon: 'beaker',
        initPrompt: `Call mcp_ck3lens-{first4}-_ck3_get_mode_instructions(mode="{mode}"{hatParam}).`
    }
};

/**
 * Generate initialization prompt with inline HAT token.
 *
 * The user clicking "Initialize Agent" IS the human authorization.
 * We sign a payload (mode + timestamp + nonce) with Sigil and embed
 * the token in the prompt. The MCP server verifies it.
 *
 * Same pattern as the original MIT — just using Sigil for signing.
 */
export function generateInitPrompt(
    mode: LensMode,
    instanceId: string | undefined,
    sigilSecret: string | undefined,
): string {
    const modeDef = MODE_DEFINITIONS[mode];
    const currentInstanceId = instanceId?.trim();
    if (!modeDef || !currentInstanceId) {
        return '';
    }

    let hatParam = '';
    if (sigilSecret) {
        const crypto = require('crypto');
        const timestamp = new Date().toISOString();
        const nonce = crypto.randomBytes(16).toString('hex');
        // Canonical payload: mode|timestamp|nonce
        const payload = `${mode}|${timestamp}|${nonce}`;
        const signature: string = crypto.createHmac('sha256', Buffer.from(sigilSecret, 'hex'))
            .update(payload)
            .digest('hex');
        // Token format: payload::signature (server splits on ::)
        hatParam = `, hat_token="${payload}::${signature}"`;
    }

    const first4 = currentInstanceId.split('-')[0];
    const currentPing = `mcp_ck3lens-${first4}-_ck3_ping`;
    const renderedPrompt = modeDef.initPrompt
        .replace(/\{first4\}/g, first4)
        .replace(/\{mode\}/g, mode)
        .replace(/\{hatParam\}/g, hatParam);

    const bootstrapSteps = [
        '1. Ping the CK3 Lens instance exposed by VS Code using ck3_ping. ' +
        `If it fails, refresh tool discovery and try ${currentPing} (instance ${currentInstanceId}).`,
        `2. After a successful ping, ${renderedPrompt.charAt(0).toLowerCase()}${renderedPrompt.slice(1)}`,
    ];

    return `${bootstrapSteps.join('\n\n')}\n`;
}

class AgentTreeItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly itemType: 'mcp-server' | 'policy-enforcement' | 'agent' | 'action' | 'info' | 'instance-id',
        public readonly collapsibleState: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly agent?: AgentInstance,
        public readonly actionCommand?: string
    ) {
        super(label, collapsibleState);
        this.contextValue = itemType;
        this.setupItem();
    }

    private setupItem(): void {
        switch (this.itemType) {
            case 'mcp-server':
                // Icon set by caller based on status
                break;
            case 'agent':
                if (this.agent) {
                    const modeDef = MODE_DEFINITIONS[this.agent.mode];
                    if (this.agent.mode === 'none') {
                        this.iconPath = new vscode.ThemeIcon('account');
                        this.description = 'initialized by VS Code';
                    } else if (this.agent.mode === 'initializing') {
                        this.iconPath = new vscode.ThemeIcon('sync~spin');
                        this.description = 'verifying...';
                    } else {
                        this.iconPath = new vscode.ThemeIcon(modeDef?.icon || 'account');
                        this.description = modeDef?.shortName || this.agent.mode;
                    }
                    // Build tooltip text based on mode
                    let modeText: string;
                    if (this.agent.mode === 'none') {
                        modeText = 'initialized by VS Code';
                    } else if (this.agent.mode === 'initializing') {
                        modeText = 'Initializing... awaiting MCP confirmation';
                    } else {
                        modeText = modeDef?.displayName || this.agent.mode;
                    }
                    this.tooltip = new vscode.MarkdownString(
                        `**${this.label}**\n\n` +
                        `Mode: ${modeText}\n\n` +
                        (this.agent.lastActivity ? `Last activity: ${this.agent.lastActivity}` : '')
                    );
                }
                break;
            case 'action':
                if (this.actionCommand) {
                    this.command = {
                        command: this.actionCommand,
                        title: this.label
                    };
                }
                break;
            case 'info':
                this.iconPath = new vscode.ThemeIcon('info');
                break;
            case 'instance-id':
                this.iconPath = new vscode.ThemeIcon('key');
                // Set command from actionCommand parameter
                if (this.actionCommand) {
                    this.command = {
                        command: this.actionCommand,
                        title: this.label
                    };
                }
                break;
        }
    }
}

export class AgentViewProvider implements vscode.TreeDataProvider<AgentTreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<AgentTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private state: AgentState;
    private disposables: vscode.Disposable[] = [];
    private mcpMonitorInterval: NodeJS.Timeout | undefined;
    private initializationRollbackTimer: NodeJS.Timeout | undefined;
    private modeFileWatcher: vscode.FileSystemWatcher | undefined;
    private readonly INITIALIZATION_ROLLBACK_TIMEOUT_MS = 120000;

    // IPC status callback — set by extension.ts after DiagnosticsServer starts
    private getIpcStatus?: () => { available: boolean; port: number };

    /**
     * Set the IPC status provider so the tree view can display IPC availability.
     */
    public setIpcStatusProvider(getter: () => { available: boolean; port: number }): void {
        this.getIpcStatus = getter;
    }

    private get instanceId(): string | undefined {
        return this.getMcpInstanceId?.();
    }

    private get modeFilePath(): string | undefined {
        const instanceId = this.instanceId;
        return instanceId
            ? getAgentModeFilePath(instanceId)
            : undefined;
    }

    /**
     * Reset the current mode while VS Code may lazily recreate the subprocess.
     */
    public onMcpDisconnected(): void {
        this.state.mcpServer = { status: 'disconnected', serverName: 'subprocess stopped' };
        this.updateAgentMode('none');
        this.refresh();
    }

    constructor(
        private readonly context: Pick<vscode.ExtensionContext, 'globalState'>,
        private readonly logger: Logger,
        private readonly getMcpInstanceId?: () => string | undefined,
        private readonly getSigilSecret?: () => string,
    ) {
        console.log('[CK3RAVEN] AgentViewProvider constructor ENTER');
        // Always start fresh - don't persist mode across sessions
        this.state = this.createFreshState();
        console.log('[CK3RAVEN] AgentViewProvider after createFreshState');
        
        // Add the event emitter to disposables for proper cleanup
        this.disposables.push(this._onDidChangeTreeData);
        this.registerCommands();
        console.log('[CK3RAVEN] AgentViewProvider after registerCommands');
        
        // Set up mode file watching if we have an instance ID
        if (this.instanceId) {
            this.setupModeFileWatcher();
            this.loadModeFromFile();
        }
        console.log('[CK3RAVEN] AgentViewProvider setupModeFileWatcher done');
        
        // Check MCP configuration on startup
        this.checkMcpConfiguration();
        console.log('[CK3RAVEN] AgentViewProvider checkMcpConfiguration done');
        
        // Start ongoing MCP monitoring (every 30 seconds)
        this.startMcpMonitoring();
        console.log('[CK3RAVEN] AgentViewProvider startMcpMonitoring done');
        console.log('[CK3RAVEN] AgentViewProvider constructor EXIT');
    }

    /**
     * Start periodic MCP server health monitoring.
     */
    private startMcpMonitoring(): void {
        // Check every 30 seconds
        this.mcpMonitorInterval = setInterval(() => {
            this.checkMcpConfiguration();
            if (this.state.mcpServer.status === 'connected') {
                this.loadModeFromFile();
            }
        }, 30000);
        
        // Also add to disposables for cleanup
        this.disposables.push({
            dispose: () => {
                if (this.mcpMonitorInterval) {
                    clearInterval(this.mcpMonitorInterval);
                }
            }
        });
    }

    /** Tool discovery is diagnostic only; lifecycle events own connection status. */
    private async checkMcpConfiguration(): Promise<void> {
        try {
            const allTools = vscode.lm.tools;
            // Match only CK3 Lens MCP tools to avoid false positives from other MCP servers.
            // VS Code truncates the tool prefix at the last hyphen-separated segment of the server name.
            // Server name: ck3lens-{instanceId} e.g. ck3lens-mh68-4f1adb
            // VS Code prefix: mcp_ck3lens-mh68-_ (drops the last segment '4f1adb')
            // The trailing '-_' = '-' from our server name's truncated hyphen + '_' from VS Code's separator.
            const shortId = this.instanceId
                ? this.instanceId.split('-').slice(0, -1).join('-') + '-'
                : '';
            const expectedPrefix = this.instanceId
                ? `mcp_ck3lens-${shortId}_`
                : 'mcp_ck3lens-';
            const ck3Tools = allTools.filter(tool => tool.name.startsWith(expectedPrefix));
            this.logger.debug(`MCP tool discovery: ${ck3Tools.length} tools listed; connection status unchanged`);
        } catch (error) {
            this.logger.error('MCP tool discovery failed:', error);
        }
    }

    private loadState(): AgentState {
        // Get stored state
        const stored = this.context.globalState.get<AgentState>('ck3lens.agentState');
        
        // Restore agents from storage, or create default
        let agents: AgentInstance[];
        if (stored?.agents && stored.agents.length > 0) {
            // Restore persisted agents but mark them as needing verification
            agents = stored.agents.map(a => ({
                ...a,
                isLocal: false  // Mark as not locally verified
            }));
            this.logger.info(`Restored ${agents.length} agent(s) from storage`);
        } else {
            // Start with default VS Code agent (no mode)
            agents = [{
                id: this.generateId(),
                mode: 'none' as LensMode,
                initializedAt: new Date().toISOString(),
                label: 'Agent',
                isLocal: false
            }];
        }
        
        return {
            mcpServer: { status: 'disconnected' },  // always start fresh — IPC events set the real status
            agents: agents,  // Use restored or default agents
            session: { id: this.generateId(), startedAt: new Date().toISOString() }
        };
    }

    private persistState(): void {
        this.context.globalState.update('ck3lens.agentState', this.state);
    }

    /**
     * Create fresh state - always starts with mode 'none'
     * We don't persist mode across sessions because the agent needs to re-initialize
     */
    private createFreshState(): AgentState {
        return {
            mcpServer: { status: 'disconnected' },
            agents: [{
                id: this.generateId(),
                mode: 'none' as LensMode,
                initializedAt: new Date().toISOString(),
                label: 'Agent',
                isLocal: true
            }],
            session: { id: this.generateId(), startedAt: new Date().toISOString() }
        };
    }

    /**
     * Watch the instance-specific mode file for real-time updates
     */
    private setupModeFileWatcher(): void {
        const fs = require('fs');
        const modeDir = CK3RAVEN_DATA_ROOT;
        
        // Create directory if needed
        if (!fs.existsSync(modeDir)) {
            fs.mkdirSync(modeDir, { recursive: true });
        }
        
        // Watch for changes to the mode file
        const pattern = new vscode.RelativePattern(modeDir, 'agent_mode_*.json');
        this.modeFileWatcher = vscode.workspace.createFileSystemWatcher(pattern);

        const loadCurrentMode = (uri: vscode.Uri) => {
            if (this.instanceId && path.basename(uri.fsPath) === getAgentModeFileName(this.instanceId)) {
                this.loadModeFromFile();
            }
        };
        this.modeFileWatcher.onDidChange(loadCurrentMode);
        this.modeFileWatcher.onDidCreate(loadCurrentMode);
        this.modeFileWatcher.onDidDelete(uri => {
            if (this.instanceId && path.basename(uri.fsPath) === getAgentModeFileName(this.instanceId)) {
                this.updateAgentMode('none');
            }
        });
        
        this.disposables.push(this.modeFileWatcher);
        this.logger.debug('AgentView mode publication subscription ready');
    }
    
    /**
     * Load agent mode from the instance-specific file
     */
    private loadModeFromFile(): void {
        const fs = require('fs');
        
        const modeFilePath = this.modeFilePath;
        if (!modeFilePath || !fs.existsSync(modeFilePath)) {
            return;
        }
        
        try {
            const content = fs.readFileSync(modeFilePath, 'utf-8');
            const data: unknown = JSON.parse(content);
            if (!data || typeof data !== 'object' || !('instance_id' in data) || data.instance_id !== this.instanceId) {
                throw new Error('Mode publication does not identify the current MCP instance');
            }
            const mode = 'mode' in data ? data.mode : undefined;
            
            if (mode === 'ck3lens' || mode === 'ck3raven-dev') {
                this.updateAgentMode(mode);
            } else if (mode === null) {
                // Mode was cleared (extension startup or session end)
                this.updateAgentMode('none');
            } else {
                throw new Error('Mode publication contains an invalid mode');
            }
        } catch (error) {
            this.logger.error('Failed to load mode file:', error);
        }
    }

    private generateId(): string {
        return Math.random().toString(36).substring(2, 10);
    }

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    /**
     * Public method to re-check MCP server status.
     * Called by refresh commands.
     */
    public async recheckMcpStatus(): Promise<void> {
        this.logger.debug('Re-checking MCP tool discovery; lifecycle connection status is unchanged');
        this.loadModeFromFile();
        await this.checkMcpConfiguration();
    }

    getTreeItem(element: AgentTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: AgentTreeItem): Thenable<AgentTreeItem[]> {
        console.log('[CK3RAVEN] AgentView.getChildren ENTER element=', element?.label);
        if (element) {
            console.log('[CK3RAVEN] AgentView.getChildren EXIT (has element)');
            return Promise.resolve([]);
        }

        const items: AgentTreeItem[] = [];

        // MCP server status is authoritative from extension lifecycle events.
        const mcpStatus = this.state.mcpServer;
        const mcpConnected = mcpStatus.status === 'connected';
        const mcpItem = new AgentTreeItem(
            mcpConnected ? 'MCP Server: connected' : 'MCP Server: disconnected',
            'mcp-server'
        );
        mcpItem.iconPath = new vscode.ThemeIcon(
            mcpConnected ? 'plug' : 'circle-slash',
            new vscode.ThemeColor(
                mcpConnected ? 'testing.iconPassed' : 'statusBarItem.errorBackground'
            )
        );
        if (mcpStatus.serverName) {
            mcpItem.description = mcpStatus.serverName;
        }
        const mcpTooltipDetails = [
            `Connection state: ${mcpStatus.status}.`,
            ...(mcpStatus.serverName ? [`Server: ${mcpStatus.serverName}.`] : []),
            ...(this.instanceId ? [`Instance: ${this.instanceId}.`] : [])
        ];
        mcpItem.tooltip = new vscode.MarkdownString(
            `**MCP Server ${mcpConnected ? 'Connected' : 'Disconnected'}**\n\n` +
            mcpTooltipDetails.join('\n\n')
        );
        items.push(mcpItem);

        // IPC Diagnostics Server status
        if (this.getIpcStatus) {
            const ipcStatus = this.getIpcStatus();
            const ipcLabel = ipcStatus.available 
                ? `IPC Server: port ${ipcStatus.port}`
                : 'IPC Server: unavailable';
            const ipcItem = new AgentTreeItem(
                ipcLabel,
                'info'
            );
            ipcItem.iconPath = new vscode.ThemeIcon(
                ipcStatus.available ? 'radio-tower' : 'warning',
                ipcStatus.available 
                    ? new vscode.ThemeColor('testing.iconPassed')
                    : new vscode.ThemeColor('problemsWarningIcon.foreground')
            );
            if (!ipcStatus.available) {
                ipcItem.tooltip = new vscode.MarkdownString(
                    '**IPC Server Unavailable**\n\n' +
                    'Script signing, Pylance diagnostics, and other VS Code-bound features will not work.\n\n' +
                    'Try reloading the VS Code window.'
                );
            }
            items.push(ipcItem);
        }

        // Instance ID display with copy action (FEAT-001)
        if (this.instanceId) {
            const instanceItem = new AgentTreeItem(
                `Instance ID: ${this.instanceId}`,
                'instance-id',
                vscode.TreeItemCollapsibleState.None,
                undefined,
                'ck3lens.agent.copyInstanceId'
            );
            instanceItem.description = 'click to copy';
            instanceItem.tooltip = new vscode.MarkdownString(
                `**MCP Server Instance ID**\n\n` +
                `ID: \`${this.instanceId}\`\n\n` +
                `Tool prefix: \`mcp_ck3lens-${this.instanceId ? this.instanceId.split('-').slice(0, -1).join('-') + '-' : '...'}_\`\n\n` +
                `Instance ID is for diagnostics/log correlation across windows.\n\n` +
                `Click to copy to clipboard or send to chat.`
            );
            items.push(instanceItem);
        }

        // Agents
        if (this.state.agents.length === 0) {
            // No agents - show initialize button
            const initItem = new AgentTreeItem(
                'Initialize Agent',
                'action',
                vscode.TreeItemCollapsibleState.None,
                undefined,
                'ck3lens.agent.initialize'
            );
            initItem.iconPath = new vscode.ThemeIcon('add');
            items.push(initItem);
        } else {
            // Show agents
            for (const agent of this.state.agents) {
                const baseLabel = agent.mode === 'none'
                    ? 'Agent'
                    : `Agent - ${MODE_DEFINITIONS[agent.mode]?.shortName || agent.mode}`;
                const agentItem = new AgentTreeItem(
                    baseLabel,
                    'agent',
                    vscode.TreeItemCollapsibleState.None,
                    agent
                );
                items.push(agentItem);
            }

            // Add sub-agent action
            const addSubItem = new AgentTreeItem(
                '+ Add Sub-agent',
                'action',
                vscode.TreeItemCollapsibleState.None,
                undefined,
                'ck3lens.agent.addSubAgent'
            );
            addSubItem.iconPath = new vscode.ThemeIcon('add');
            items.push(addSubItem);
        }

        return Promise.resolve(items);
    }

    private registerCommands(): void {
        // Initialize agent (default mode)
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.initialize', () => {
                this.initializeAgent();
            })
        );

        // Initialize with specific mode
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.initializeWithMode', async () => {
                const items = Object.entries(MODE_DEFINITIONS).map(([id, def]) => ({
                    label: def.displayName,
                    description: def.description,
                    id
                }));

                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select agent mode',
                    title: 'Initialize Agent with Mode'
                });

                if (selected) {
                    await this.initializeAgentWithMode(selected.id as LensMode);
                }
            })
        );

        // Re-initialize agent (context menu or palette)
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.reinitialize', async (_item?: AgentTreeItem) => {
                // _item is only provided when invoked from the tree view inline icon.
                // The handler doesn't actually need it — it just shows a mode picker.
                const items = Object.entries(MODE_DEFINITIONS).map(([id, def]) => ({
                    label: def.displayName,
                    description: def.description,
                    id
                }));

                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select new mode',
                    title: 'Re-initialize Agent'
                });

                if (selected) {
                    await this.initializeAgentWithMode(selected.id as LensMode);
                }
            })
        );

        // Add sub-agent
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.addSubAgent', async () => {
                const items = Object.entries(MODE_DEFINITIONS).map(([id, def]) => ({
                    label: def.displayName,
                    description: def.description,
                    id
                }));

                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select sub-agent mode',
                    title: 'Add Sub-agent'
                });

                if (selected) {
                    await this.addSubAgent(selected.id as LensMode);
                }
            })
        );

        // Clear all agents (reset to default)
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.clearAll', () => {
                // Reset to default VS Code agent (not initialized with a mode)
                const defaultAgent: AgentInstance = {
                    id: this.generateId(),
                    mode: 'none',
                    initializedAt: new Date().toISOString(),
                    label: 'Agent',
                    isLocal: false
                };
                this.state.agents = [defaultAgent];
                this.persistState();
                this.refresh();
                this.logger.info('All agents cleared');
            })
        );

        // FEAT-001: Copy instance ID to clipboard or send to chat
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.copyInstanceId', async () => {
                if (!this.instanceId) {
                    vscode.window.showWarningMessage('Instance ID not available');
                    return;
                }

                const shortId = this.instanceId.split('-').slice(0, -1).join('-') + '-';
                const toolPrefix = `mcp_ck3lens-${shortId}_`;
                const actions = [
                    { label: '$(clippy) Copy Instance ID', action: 'copy_id' },
                    { label: '$(comment) Copy Tool Prefix', action: 'copy_prefix' },
                    { label: '$(comment-discussion) Send to Chat', action: 'chat' },
                ];

                const selected = await vscode.window.showQuickPick(actions, {
                    placeHolder: `Instance ID: ${this.instanceId}`,
                    title: 'Copy MCP Instance ID'
                });

                if (!selected) return;

                switch (selected.action) {
                    case 'copy_id':
                        await vscode.env.clipboard.writeText(this.instanceId);
                        vscode.window.showInformationMessage(`Copied instance ID: ${this.instanceId}`);
                        break;
                    case 'copy_prefix':
                        await vscode.env.clipboard.writeText(toolPrefix);
                        vscode.window.showInformationMessage(`Copied tool prefix: ${toolPrefix}`);
                        break;
                    case 'chat':
                        const message = `[${this.instanceId}] prefix=${toolPrefix}`;
                        await vscode.commands.executeCommand('workbench.action.chat.open', { query: message });
                        vscode.window.showInformationMessage('Instance ID sent to chat');
                        break;
                }
            })
        );

        // Mint HAT (Human Authorization Token)
        this.disposables.push(
            vscode.commands.registerCommand('ck3lens.agent.mintHat', async () => {
                await this.mintHat();
            })
        );
    }

    /**
     * Approve a HAT (Human Authorization Token) request.
     *
     * Ephemeral flow — no token passes through chat:
     * 1. Agent writes hat_request.json (via contract gate or ck3_protect)
     * 2. User clicks shield icon → this method runs
     * 3. Extension reads request, shows approval dialog
     * 4. User approves → extension HMAC-signs with in-memory secret
     * 5. Extension writes hat_approval.json, deletes hat_request.json
     * 6. MCP server consumes approval on next contract open attempt
     *
     * The secret never touches disk. The approval is consumed (deleted)
     * on first read by the MCP server.
     */
    private async mintHat(): Promise<void> {
        const crypto = require('crypto');
        const pathMod = require('path');
        const fs = require('fs');

        const sigilSecret = this.getSigilSecret?.();
        if (!sigilSecret) {
            vscode.window.showErrorMessage('Sigil secret not available. MCP server may not be running.');
            return;
        }

        const configDir = pathMod.join(CK3RAVEN_DATA_ROOT, 'config');
        const requestPath = pathMod.join(configDir, 'hat_request.json');
        const approvalPath = pathMod.join(configDir, 'hat_approval.json');

        // Read agent's request template
        if (!fs.existsSync(requestPath)) {
            vscode.window.showInformationMessage(
                'No pending HAT request. The agent will create one when it needs approval for protected files.'
            );
            return;
        }

        let request: any;
        try {
            request = JSON.parse(fs.readFileSync(requestPath, 'utf-8'));
        } catch (err: any) {
            vscode.window.showErrorMessage(`Failed to read HAT request: ${err.message}`);
            return;
        }

        const intent: string = request.intent || '(no intent specified)';
        const protectedPaths: string[] = request.protected_paths || [];
        const rootCategory: string = request.root_category || '';

        // Build approval dialog message
        const pathsDisplay = protectedPaths.length > 0
            ? protectedPaths.map(p => `  \u2022 ${p}`).join('\n')
            : '  (none specified)';

        const confirm = await vscode.window.showWarningMessage(
            [
                'Approve HAT request?',
                '',
                `Intent: ${intent}`,
                `Scope: ${rootCategory || 'N/A'}`,
                `Protected files:`,
                pathsDisplay,
                '',
                'This allows the agent to open ONE contract touching these files.',
            ].join('\n'),
            { modal: true },
            'Approve'
        );

        if (confirm !== 'Approve') {
            // Clean up rejected request
            try { fs.unlinkSync(requestPath); } catch { /* ignore */ }
            vscode.window.showInformationMessage('HAT request rejected.');
            return;
        }

        // Generate approval — all signing in extension memory
        const approvedAt = new Date().toISOString();
        const nonce = crypto.randomBytes(16).toString('hex');

        // Canonical JSON must match Python's json.dumps(sort_keys=True, separators=(",",":"))
        // Keys in alphabetical order: approved_at, intent, nonce, protected_paths, root_category
        const canonicalObj: Record<string, any> = {
            approved_at: approvedAt,
            intent: intent,
            nonce: nonce,
            protected_paths: protectedPaths,
            root_category: rootCategory,
        };
        const canonicalJson = JSON.stringify(canonicalObj);

        // HMAC-SHA256 with Sigil secret (in-memory only)
        const signature: string = crypto.createHmac('sha256', Buffer.from(sigilSecret, 'hex'))
            .update(canonicalJson)
            .digest('hex');

        // Write approval file (consumed by MCP on next contract open)
        const approval = {
            ...canonicalObj,
            signature: signature,
        };

        try {
            fs.mkdirSync(configDir, { recursive: true });
            fs.writeFileSync(approvalPath, JSON.stringify(approval, null, 2));

            // Delete consumed request
            fs.unlinkSync(requestPath);
        } catch (err: any) {
            vscode.window.showErrorMessage(`Failed to write HAT approval: ${err.message}`);
            return;
        }

        this.logger.info(`HAT approved: intent="${intent}", paths=${protectedPaths.length}`);
        vscode.window.showInformationMessage(
            `HAT approved. The agent can now retry its operation.\n\nIntent: ${intent}`
        );
    }

    /**
     * Initialize agent in default mode
     */
    private initializeAgent(): void {
        const newAgent: AgentInstance = {
            id: this.generateId(),
            mode: 'none',
            initializedAt: new Date().toISOString(),
            label: 'Agent',
            isLocal: true
        };

        // Replace existing primary agent
        this.state.agents = this.state.agents.filter(a => a.label.startsWith('Sub-'));
        this.state.agents.unshift(newAgent);

        this.persistState();
        this.refresh();
        this.logger.info('Agent initialized in default mode');
    }

    private getInitializationContext(): { instanceId: string; sigilSecret: string } | undefined {
        const instanceId = this.instanceId?.trim();
        if (!instanceId) {
            vscode.window.showErrorMessage('Cannot initialize agent: MCP instance ID is not available.');
            return undefined;
        }

        const sigilSecret = this.getSigilSecret?.()?.trim();
        if (!sigilSecret) {
            vscode.window.showErrorMessage('Cannot initialize agent: Sigil secret is not available.');
            return undefined;
        }

        return { instanceId, sigilSecret };
    }

    private startInitializationRollbackTimer(): void {
        this.clearInitializationRollbackTimer();
        this.initializationRollbackTimer = setTimeout(() => {
            this.initializationRollbackTimer = undefined;
            const primaryAgent = this.state.agents.find(a => !a.label.startsWith('Sub-'));
            if (primaryAgent?.mode !== 'initializing') {
                return;
            }

            this.loadModeFromFile();
            const confirmedMode = this.state.agents.find(a => !a.label.startsWith('Sub-'))?.mode;
            if (confirmedMode === 'ck3lens' || confirmedMode === 'ck3raven-dev') {
                return;
            }
            this.updateAgentMode('none');
            this.logger.warn('Agent initialization was not confirmed after 120 seconds; display set to uninitialized');
            void vscode.window.showWarningMessage(
                'Agent initialization was not confirmed after 120 seconds. The display is uninitialized; server authorization was not changed.'
            );
        }, this.INITIALIZATION_ROLLBACK_TIMEOUT_MS);
    }

    private clearInitializationRollbackTimer(): void {
        if (this.initializationRollbackTimer) {
            clearTimeout(this.initializationRollbackTimer);
            this.initializationRollbackTimer = undefined;
        }
    }

    /**
     * Initialize agent with specific mode
     * 
     * Sets status to 'initializing' immediately for user feedback.
     * The mode file watcher will update to actual mode when MCP confirms,
     * or the rollback timer will reset the mode if initialization fails.
     */
    private async initializeAgentWithMode(mode: LensMode): Promise<void> {
        const modeDef = MODE_DEFINITIONS[mode];
        if (!modeDef) return;

        const initializationContext = this.getInitializationContext();
        if (!initializationContext) return;

        this.startInitializationRollbackTimer();
        this.updateAgentMode('initializing');

        this.logger.info(`Initialization requested for mode: ${mode}`);

        // Open chat with init prompt — includes signed inline HAT token
        try {
            await vscode.commands.executeCommand('workbench.action.chat.open', {
                query: generateInitPrompt(
                    mode,
                    initializationContext.instanceId,
                    initializationContext.sigilSecret,
                )
            });
        } catch (error) {
            this.updateAgentMode('none');
            this.logger.error('Failed to open chat for agent initialization:', error);
            void vscode.window.showErrorMessage('Failed to open chat for agent initialization.');
            return;
        }

        vscode.window.showInformationMessage(
            `${modeDef.shortName} mode: Press Enter to send the initialization prompt`
        );
    }

    /**
     * Add a sub-agent
     * 
     * Sets status to 'initializing' for user feedback, then opens new chat.
     * The mode file watcher will update status when MCP confirms success.
     */
    private async addSubAgent(mode: LensMode): Promise<void> {
        const modeDef = MODE_DEFINITIONS[mode];
        if (!modeDef) return;

        const initializationContext = this.getInitializationContext();
        if (!initializationContext) return;

        this.startInitializationRollbackTimer();
        this.updateAgentMode('initializing');

        this.logger.info(`Sub-agent initialization requested for mode: ${mode}`);

        // Open new chat with init prompt — includes signed inline HAT token
        try {
            await vscode.commands.executeCommand('workbench.action.chat.newChat');
            await new Promise(resolve => setTimeout(resolve, 100));
            await vscode.commands.executeCommand('workbench.action.chat.open', {
                query: generateInitPrompt(
                    mode,
                    initializationContext.instanceId,
                    initializationContext.sigilSecret,
                )
            });
        } catch (error) {
            this.updateAgentMode('none');
            this.logger.error('Failed to open chat for sub-agent initialization:', error);
            void vscode.window.showErrorMessage('Failed to open chat for sub-agent initialization.');
            return;
        }

        vscode.window.showInformationMessage(
            `Sub-agent ${modeDef.shortName}: Press Enter to send the initialization prompt (new chat created)`
        );
    }

    /**
     * Set MCP Server connection status
     */
    public setMcpServerStatus(status: ConnectionStatus, serverName?: string): void {
        this.state.mcpServer = { status, serverName };
        if (status === 'connected') {
            this.loadModeFromFile();
        }
        this.persistState();
        this.refresh();
    }

    /**
     * Update the displayed agent mode from the MCP mode publication or UI actions.
     * Always updates mode - allows switching between modes
     */
    public updateAgentMode(mode: LensMode): void {
        if (mode !== 'initializing') {
            this.clearInitializationRollbackTimer();
        }

        const primaryAgent = this.state.agents.find(a => !a.label.startsWith('Sub-'));
        if (primaryAgent) {
            if (primaryAgent.mode !== mode) {
                this.logger.debug(`AgentView mode display changed: ${primaryAgent.mode} -> ${mode}`);
                primaryAgent.mode = mode;
                primaryAgent.initializedAt = new Date().toISOString();
                this.persistState();
                this.refresh();
            }
        } else {
            // No agent exists, create one
            const newAgent: AgentInstance = {
                id: this.generateId(),
                mode: mode,
                initializedAt: new Date().toISOString(),
                label: 'Agent',
                isLocal: true
            };
            this.state.agents.unshift(newAgent);
            this.persistState();
            this.refresh();
            this.logger.debug(`AgentView entry created with mode: ${mode}`);
        }
    }

    /**
     * Get current state
     */
    public getState(): AgentState {
        return { ...this.state };
    }

    dispose(): void {
        this.clearInitializationRollbackTimer();
        while (this.disposables.length) {
            const d = this.disposables.pop();
            if (d) d.dispose();
        }
    }
}
