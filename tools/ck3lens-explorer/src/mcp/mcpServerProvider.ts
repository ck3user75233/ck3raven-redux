import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { McpGeneration } from './mcpLifecycle';
import { McpHttpEndpoint } from './mcpProcess';
import { Logger } from '../utils/logger';
import { CK3RAVEN_DATA_ROOT, CK3RAVEN_PYTHON, ROOT_REPO as CONFIGURED_ROOT_REPO } from '../config/runtimePaths';

export function getRootRepo(): string | undefined {
    if (!CONFIGURED_ROOT_REPO || !fs.existsSync(path.join(CONFIGURED_ROOT_REPO, 'pyproject.toml'))) {
        return undefined;
    }
    return CONFIGURED_ROOT_REPO;
}

export function findCk3RavenPython(): string | undefined {
    return CK3RAVEN_PYTHON && fs.existsSync(CK3RAVEN_PYTHON) ? CK3RAVEN_PYTHON : undefined;
}

/** Publishes a connection to an already-running process; never a launch command. */
export class CK3LensMcpServerProvider implements vscode.McpServerDefinitionProvider<vscode.McpHttpServerDefinition>, vscode.Disposable {
    private disposed = false;

    constructor(
        private readonly generation: McpGeneration,
        private readonly endpoint: McpHttpEndpoint,
    ) {}

    getInstanceId(): string {
        return this.generation.instanceId;
    }

    getSigilSecret(): string {
        return this.generation.sigilSecret;
    }

    provideMcpServerDefinitions(_token: vscode.CancellationToken): vscode.McpHttpServerDefinition[] {
        if (this.disposed) {
            return [];
        }
        return [new vscode.McpHttpServerDefinition(
            `ck3lens-${this.generation.instanceId}`,
            vscode.Uri.parse(this.endpoint.url),
            { 'x-ck3lens-token': this.endpoint.token },
            this.generation.instanceId,
        )];
    }

    resolveMcpServerDefinition(
        server: vscode.McpHttpServerDefinition,
        token: vscode.CancellationToken,
    ): vscode.McpHttpServerDefinition | undefined {
        return this.provideMcpServerDefinitions(token).find(definition => definition.label === server.label);
    }

    dispose(): void {
        this.disposed = true;
    }
}

export interface McpProviderRegistration {
    provider: CK3LensMcpServerProvider;
    registration: vscode.Disposable;
}

export function registerMcpServerProvider(
    logger: Logger,
    generation: McpGeneration,
    endpoint: McpHttpEndpoint,
): McpProviderRegistration {
    if (!vscode.lm?.registerMcpServerDefinitionProvider || !vscode.McpHttpServerDefinition) {
        throw new Error('VS Code MCP HTTP definition provider API is unavailable');
    }
    for (const name of ['mit_token.txt', 'hat_secret.key']) {
        const legacyPath = path.join(CK3RAVEN_DATA_ROOT, 'config', name);
        try {
            if (fs.existsSync(legacyPath)) {
                fs.unlinkSync(legacyPath);
                logger.info(`Deleted legacy file: ${name}`);
            }
        } catch (error) {
            logger.error(`Failed to delete legacy file: ${name}`, error);
        }
    }
    const provider = new CK3LensMcpServerProvider(generation, endpoint);
    const registration = vscode.lm.registerMcpServerDefinitionProvider('ck3lens-mcp', provider);
    logger.info(`HTTP MCP provider registered: instance=${generation.instanceId} url=${endpoint.url}`);
    return { provider, registration };
}
