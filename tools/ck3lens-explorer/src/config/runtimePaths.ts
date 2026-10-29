import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

export const CK3RAVEN_DATA_ROOT = process.env.CK3RAVEN_DATA || path.join(os.homedir(), '.ck3raven');
export const WORKSPACE_CONFIG_PATH = path.join(CK3RAVEN_DATA_ROOT, 'config', 'workspace.toml');
export const CK3RAVEN_DATABASE_PATH = path.join(CK3RAVEN_DATA_ROOT, 'ck3raven.db');

export function getAgentModeFileName(instanceId: string): string {
    return `agent_mode_${instanceId.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`;
}

export function getAgentModeFilePath(instanceId: string): string {
    return path.join(CK3RAVEN_DATA_ROOT, getAgentModeFileName(instanceId));
}

export function getIpcPortFilePath(instanceId?: string): string {
    return path.join(CK3RAVEN_DATA_ROOT, instanceId ? `ipc_port_${instanceId}` : 'ipc_port');
}

function readWorkspaceConfig(): ReadonlyMap<string, string> {
    if (!fs.existsSync(WORKSPACE_CONFIG_PATH)) {
        return new Map();
    }

    const values = new Map<string, string>();
    const content = fs.readFileSync(WORKSPACE_CONFIG_PATH, 'utf8');
    for (const match of content.matchAll(/^([a-z_]+)\s*=\s*"([^"]*)"\s*$/gm)) {
        values.set(match[1], match[2]);
    }
    return values;
}

const WORKSPACE_CONFIG = readWorkspaceConfig();

export const ROOT_REPO = WORKSPACE_CONFIG.get('root_repo');
export const ROOT_GAME = WORKSPACE_CONFIG.get('root_game');
export const ROOT_STEAM = WORKSPACE_CONFIG.get('root_steam');
export const ROOT_USER_DOCS = WORKSPACE_CONFIG.get('root_user_docs');
export const ROOT_VSCODE = WORKSPACE_CONFIG.get('root_vscode');
export const CK3RAVEN_PYTHON = ROOT_REPO
    ? path.join(ROOT_REPO, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    : undefined;
