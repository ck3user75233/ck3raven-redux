"""
Canonical Reply Code Registry.

STATUS: AUTHORITATIVE
PURPOSE: Single source of truth for all reply codes in ck3lens MCP.

Format: LAYER-AREA-TYPE-NNN

LAYER values (exhaustive, no additions without architecture review):
    WA  - World Adapter: Resolution, visibility, world-mapping
    EN  - Enforcement: Governance, authorization, policy
    CT  - Contract System: Contract lifecycle only
    MCP - Infrastructure: Transport, system failures

LAYER ownership rules (DECISION-LOCKED):
    WA  → May emit: S, I, E    Must NOT emit: D
    EN  → May emit: S, D, E    Must NOT emit: I
    CT  → May emit: S, I, E    Must NOT emit: D
    MCP → May emit: S, I, E    Must NOT emit: D

AREA values (extensible via registry process):
    SYS   - System operations
    RES   - Resolution (path/symbol lookup)
    VIS   - Visibility (playset scope)
    READ  - Read operations
    WRITE - Write operations
    EXEC  - Command execution
    OPEN  - Contract/session open
    CLOSE - Contract/session close
    VAL   - Validation
    DB    - Database operations
    PARSE - Parsing operations
    GIT   - Git operations
    QB    - QBuilder daemon / build system

TYPE values (exhaustive):
    S - Success
    I - Invalid (caller error, recoverable)
    D - Denied (governance refusal)
    E - Error (system failure)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar
import re


class Layer(str, Enum):
    """Semantic ownership layer. No additions without architecture review."""
    WA = "WA"    # World Adapter
    EN = "EN"    # Enforcement
    CT = "CT"    # Contract System
    MCP = "MCP"  # Infrastructure


class Area(str, Enum):
    """Functional domain. Extensible via registry process.
    
    AREA is orthogonal to LAYER. The same AREA may appear under multiple LAYERs.
    AREA describes WHAT the operation is about, not WHO made the decision.
    """
    SYS = "SYS"      # System operations / fallback
    RES = "RES"      # Resolution (paths, identifiers)
    VIS = "VIS"      # Visibility / scopes
    IO = "IO"        # Generic IO not cleanly READ/WRITE
    READ = "READ"    # Read operations
    WRITE = "WRITE"  # Write operations
    EXEC = "EXEC"    # Command execution
    OPEN = "OPEN"    # Contract/session open
    CLOSE = "CLOSE"  # Contract/session close
    VAL = "VAL"      # Validation (schema/structural, not denial)
    GATE = "GATE"    # Precondition checks
    DB = "DB"        # Database operations
    PARSE = "PARSE"  # Parsing / AST / syntax
    GIT = "GIT"      # Git operations
    LOG = "LOG"      # Logs, diagnostics
    CFG = "CFG"      # Configuration, setup, mode, state
    DIR = "DIR"      # Directory navigation (v2 canonical addressing)
    SIGN = "SIGN"    # Script signing for exec authorization
    QB = "QB"        # QBuilder daemon / build system


class ReplyType(str, Enum):
    """Reply type. Fixed, no additions."""
    S = "S"  # Success
    I = "I"  # Invalid
    D = "D"  # Denied
    E = "E"  # Error


# Layer ownership rules (DECISION-LOCKED)
LAYER_ALLOWED_TYPES: dict[Layer, frozenset[ReplyType]] = {
    Layer.WA: frozenset({ReplyType.S, ReplyType.I, ReplyType.E}),
    Layer.EN: frozenset({ReplyType.S, ReplyType.I, ReplyType.D, ReplyType.E}),
    Layer.CT: frozenset({ReplyType.S, ReplyType.I, ReplyType.E}),
    Layer.MCP: frozenset({ReplyType.S, ReplyType.E, ReplyType.I}),  # S for ungoverned ops
}


@dataclass(frozen=True)
class ReplyCode:
    """A canonical reply code with metadata."""
    code: str
    layer: Layer
    area: Area
    reply_type: ReplyType
    number: int
    message_key: str
    description: str

    def __post_init__(self):
        # Validate format matches components
        expected = f"{self.layer.value}-{self.area.value}-{self.reply_type.value}-{self.number:03d}"
        if self.code != expected:
            raise ValueError(f"Code {self.code!r} doesn't match components: expected {expected}")
        
        # Validate layer ownership
        if self.reply_type not in LAYER_ALLOWED_TYPES[self.layer]:
            raise ValueError(
                f"Layer {self.layer.value} cannot emit type {self.reply_type.value}. "
                f"Allowed: {[t.value for t in LAYER_ALLOWED_TYPES[self.layer]]}"
            )


class Codes:
    """
    Canonical reply code registry.
    
    All codes are defined here. No ad-hoc codes in tool implementations.
    """
    
    # =========================================================================
    # WA (World Adapter) - Resolution, visibility, world-mapping
    # May emit: S, I, E    Must NOT emit: D
    # =========================================================================
    
    # Resolution
    WA_RES_S_001 = ReplyCode("WA-RES-S-001", Layer.WA, Area.RES, ReplyType.S, 1,
        "RESOLUTION_OK", "Path/symbol resolved successfully")
    WA_RES_I_001 = ReplyCode("WA-RES-I-001", Layer.WA, Area.RES, ReplyType.I, 1,
        "PATH_NOT_FOUND", "Path not found")
    WA_RES_I_002 = ReplyCode("WA-RES-I-002", Layer.WA, Area.RES, ReplyType.I, 2,
        "SYMBOL_NOT_FOUND", "Symbol not found in index")
    WA_RES_I_003 = ReplyCode("WA-RES-I-003", Layer.WA, Area.RES, ReplyType.I, 3,
        "PATH_NOT_VISIBLE", "Path exists but not in current visibility scope")
    WA_RES_E_001 = ReplyCode("WA-RES-E-001", Layer.WA, Area.RES, ReplyType.E, 1,
        "RESOLUTION_ERROR", "Unexpected error during resolution")
    
    # Visibility
    WA_VIS_S_001 = ReplyCode("WA-VIS-S-001", Layer.WA, Area.VIS, ReplyType.S, 1,
        "VISIBILITY_OK", "Visibility/playset operation complete")
    WA_VIS_I_001 = ReplyCode("WA-VIS-I-001", Layer.WA, Area.VIS, ReplyType.I, 1,
        "NO_PLAYSET", "No active playset configured")
    WA_VIS_I_002 = ReplyCode("WA-VIS-I-002", Layer.WA, Area.VIS, ReplyType.I, 2,
        "PLAYSET_NOT_FOUND", "Requested playset not found")
    WA_VIS_I_003 = ReplyCode("WA-VIS-I-003", Layer.WA, Area.VIS, ReplyType.I, 3,
        "PLAYSET_SCHEMA_INVALID", "Playset JSON failed schema validation (fail closed)")
    WA_VIS_E_001 = ReplyCode("WA-VIS-E-001", Layer.WA, Area.VIS, ReplyType.E, 1,
        "VISIBILITY_ERROR", "Unexpected error in visibility operation")
    
    # Read
    WA_READ_S_001 = ReplyCode("WA-READ-S-001", Layer.WA, Area.READ, ReplyType.S, 1,
        "READ_OK", "Read operation complete")
    WA_READ_I_001 = ReplyCode("WA-READ-I-001", Layer.WA, Area.READ, ReplyType.I, 1,
        "READ_TARGET_MISSING", "Read target does not exist")
    WA_READ_I_002 = ReplyCode("WA-READ-I-002", Layer.WA, Area.READ, ReplyType.I, 2,
        "READ_TYPE_UNSUPPORTED", "File type not supported for this operation")
    WA_READ_E_001 = ReplyCode("WA-READ-E-001", Layer.WA, Area.READ, ReplyType.E, 1,
        "READ_ERROR", "Unexpected error during read")
    
    # Write
    WA_WRITE_S_001 = ReplyCode("WA-WRITE-S-001", Layer.WA, Area.WRITE, ReplyType.S, 1,
        "WRITE_OK", "Write operation complete")
    WA_WRITE_I_001 = ReplyCode("WA-WRITE-I-001", Layer.WA, Area.WRITE, ReplyType.I, 1,
        "WRITE_TARGET_INVALID", "Write target path is malformed")
    WA_WRITE_E_001 = ReplyCode("WA-WRITE-E-001", Layer.WA, Area.WRITE, ReplyType.E, 1,
        "WRITE_ERROR", "Unexpected error during write")
    
    # Database
    WA_DB_S_001 = ReplyCode("WA-DB-S-001", Layer.WA, Area.DB, ReplyType.S, 1,
        "DB_OP_OK", "Database operation complete")
    WA_DB_I_001 = ReplyCode("WA-DB-I-001", Layer.WA, Area.DB, ReplyType.I, 1,
        "DB_INPUT_MISSING", "Required database input missing")
    WA_DB_I_002 = ReplyCode("WA-DB-I-002", Layer.WA, Area.DB, ReplyType.I, 2,
        "DB_TABLE_UNKNOWN", "Unknown table name")
    WA_DB_I_003 = ReplyCode("WA-DB-I-003", Layer.WA, Area.DB, ReplyType.I, 3,
        "DB_QUERY_INVALID", "Invalid SQL query (syntax error, unknown column, bad table)")
    WA_DB_E_001 = ReplyCode("WA-DB-E-001", Layer.WA, Area.DB, ReplyType.E, 1,
        "DB_ERROR", "Database error (unexpected system failure)")
    
    # Parse
    WA_PARSE_S_001 = ReplyCode("WA-PARSE-S-001", Layer.WA, Area.PARSE, ReplyType.S, 1,
        "PARSE_OK", "Parse successful")
    WA_PARSE_I_001 = ReplyCode("WA-PARSE-I-001", Layer.WA, Area.PARSE, ReplyType.I, 1,
        "PARSE_SYNTAX_ERROR", "Syntax errors found (recoverable)")
    WA_PARSE_E_001 = ReplyCode("WA-PARSE-E-001", Layer.WA, Area.PARSE, ReplyType.E, 1,
        "PARSE_ERROR", "Unexpected parse error")
    
    # System
    WA_SYS_S_001 = ReplyCode("WA-SYS-S-001", Layer.WA, Area.SYS, ReplyType.S, 1,
        "SYS_OP_OK", "System operation complete")
    WA_SYS_S_002 = ReplyCode("WA-SYS-S-002", Layer.WA, Area.SYS, ReplyType.S, 2,
        "SYS_PREVIEW_OK", "Preview/dry-run complete")
    WA_SYS_I_001 = ReplyCode("WA-SYS-I-001", Layer.WA, Area.SYS, ReplyType.I, 1,
        "SERVICE_UNAVAILABLE", "Agent mode not initialized — ask the user to initialize you")
    WA_SYS_E_001 = ReplyCode("WA-SYS-E-001", Layer.WA, Area.SYS, ReplyType.E, 1,
        "SYS_ERROR", "System error")
    
    # Validation
    WA_VAL_S_001 = ReplyCode("WA-VAL-S-001", Layer.WA, Area.VAL, ReplyType.S, 1,
        "VALIDATION_OK", "Validation passed")
    WA_VAL_I_001 = ReplyCode("WA-VAL-I-001", Layer.WA, Area.VAL, ReplyType.I, 1,
        "VALIDATION_FAILED", "Validation failed (caller can fix)")
    WA_VAL_E_001 = ReplyCode("WA-VAL-E-001", Layer.WA, Area.VAL, ReplyType.E, 1,
        "VALIDATION_ERROR", "Unexpected validation error")
    
    # Log / Diagnostics
    WA_LOG_S_001 = ReplyCode("WA-LOG-S-001", Layer.WA, Area.LOG, ReplyType.S, 1,
        "LOG_OP_OK", "Log operation complete")
    WA_LOG_I_001 = ReplyCode("WA-LOG-I-001", Layer.WA, Area.LOG, ReplyType.I, 1,
        "LOG_NOT_FOUND", "Log not found")
    WA_LOG_E_001 = ReplyCode("WA-LOG-E-001", Layer.WA, Area.LOG, ReplyType.E, 1,
        "LOG_ERROR", "Log operation error")
    
    # Configuration / Mode / State
    WA_CFG_S_001 = ReplyCode("WA-CFG-S-001", Layer.WA, Area.CFG, ReplyType.S, 1,
        "CFG_OP_OK", "Configuration operation complete")
    WA_CFG_I_001 = ReplyCode("WA-CFG-I-001", Layer.WA, Area.CFG, ReplyType.I, 1,
        "CFG_DEPRECATED", "Configuration/feature deprecated")
    WA_CFG_E_001 = ReplyCode("WA-CFG-E-001", Layer.WA, Area.CFG, ReplyType.E, 1,
        "CFG_ERROR", "Configuration error")
    
    # Generic IO (probes, stats, external systems)
    WA_IO_S_001 = ReplyCode("WA-IO-S-001", Layer.WA, Area.IO, ReplyType.S, 1,
        "IO_OP_OK", "IO operation complete")
    WA_IO_I_001 = ReplyCode("WA-IO-I-001", Layer.WA, Area.IO, ReplyType.I, 1,
        "IO_UNAVAILABLE", "IO target unavailable")
    WA_IO_E_001 = ReplyCode("WA-IO-E-001", Layer.WA, Area.IO, ReplyType.E, 1,
        "IO_ERROR", "IO error")
    
    # Directory Navigation (v2 canonical addressing)
    WA_DIR_S_001 = ReplyCode("WA-DIR-S-001", Layer.WA, Area.DIR, ReplyType.S, 1,
        "DIR_OP_OK", "Directory operation complete")
    WA_DIR_I_001 = ReplyCode("WA-DIR-I-001", Layer.WA, Area.DIR, ReplyType.I, 1,
        "DIR_INVALID", "Directory operation input invalid")
    WA_DIR_E_001 = ReplyCode("WA-DIR-E-001", Layer.WA, Area.DIR, ReplyType.E, 1,
        "DIR_LEAK", "Host path leaked in directory output (internal error)")
    
    # =========================================================================
    # EN (Enforcement) - Governance, authorization, policy
    # May emit: S, D, E    Must NOT emit: I
    # =========================================================================
    
    # Gate (precondition checks - EN owns ALL denial decisions)
    # D-001: No rule found in operations matrix (also fallback for unmapped conditions)
    # D-002: has_contract condition failed (contract required)
    # D-003: exec_gate condition failed (signed script + open contract required)
    EN_GATE_S_001 = ReplyCode("EN-GATE-S-001", Layer.EN, Area.GATE, ReplyType.S, 1,
        "GATE_OK", "Precondition check passed")
    EN_GATE_D_001 = ReplyCode("EN-GATE-D-001", Layer.EN, Area.GATE, ReplyType.D, 1,
        "NO_RULE", "No matching operation rule found or unmapped condition failed")
    EN_GATE_D_002 = ReplyCode("EN-GATE-D-002", Layer.EN, Area.GATE, ReplyType.D, 2,
        "CONTRACT_REQUIRED", "Active contract required for this operation")
    EN_GATE_D_003 = ReplyCode("EN-GATE-D-003", Layer.EN, Area.GATE, ReplyType.D, 3,
        "EXEC_GATE_REQUIRED", "Valid signed script with open contract required")
    EN_GATE_E_001 = ReplyCode("EN-GATE-E-001", Layer.EN, Area.GATE, ReplyType.E, 1,
        "GATE_ERROR", "Error during precondition check")
    
    # Write enforcement
    EN_WRITE_S_001 = ReplyCode("EN-WRITE-S-001", Layer.EN, Area.WRITE, ReplyType.S, 1,
        "WRITE_AUTHORIZED", "Write authorized by policy")
    EN_WRITE_S_002 = ReplyCode("EN-WRITE-S-002", Layer.EN, Area.WRITE, ReplyType.S, 2,
        "WRITE_CONTENT_MISMATCH", "Write executed but expected content was not found")
    EN_WRITE_D_001 = ReplyCode("EN-WRITE-D-001", Layer.EN, Area.WRITE, ReplyType.D, 1,
        "WRITE_DENIED", "Write denied by policy")
    EN_WRITE_D_002 = ReplyCode("EN-WRITE-D-002", Layer.EN, Area.WRITE, ReplyType.D, 2,
        "CONTRACT_REQUIRED", "Active contract required for this write/delete")
    EN_WRITE_E_001 = ReplyCode("EN-WRITE-E-001", Layer.EN, Area.WRITE, ReplyType.E, 1,
        "WRITE_ENFORCEMENT_ERROR", "Unexpected error in write enforcement")
    
    # Read enforcement
    EN_READ_S_001 = ReplyCode("EN-READ-S-001", Layer.EN, Area.READ, ReplyType.S, 1,
        "READ_OK", "Read authorized by policy")
    EN_READ_S_002 = ReplyCode("EN-READ-S-002", Layer.EN, Area.READ, ReplyType.S, 2,
        "READ_NO_FILE", "There is no file to read at this path, so no content will be returned")
    
    # Parse results
    EN_PARSE_I_001 = ReplyCode("EN-PARSE-I-001", Layer.EN, Area.PARSE, ReplyType.I, 1,
        "WRITE_BLOCKED_BY_PARSE_ERRORS", "File was not written because CK3 syntax validation failed")
    EN_PARSE_S_001 = ReplyCode("EN-PARSE-S-001", Layer.EN, Area.PARSE, ReplyType.S, 1,
        "PARSE_COMPLETED_WITH_ERRORS", "Parse executed but syntax errors found")
    
    # Exec denials
    EN_EXEC_S_001 = ReplyCode("EN-EXEC-S-001", Layer.EN, Area.EXEC, ReplyType.S, 1,
        "EXEC_OK", "Command executed")
    EN_EXEC_S_002 = ReplyCode("EN-EXEC-S-002", Layer.EN, Area.EXEC, ReplyType.S, 2,
        "EXEC_DRY_RUN", "Dry run - would be allowed")
    EN_EXEC_D_001 = ReplyCode("EN-EXEC-D-001", Layer.EN, Area.EXEC, ReplyType.D, 1,
        "EXEC_DENIED", "Command execution denied")
    EN_EXEC_E_001 = ReplyCode("EN-EXEC-E-001", Layer.EN, Area.EXEC, ReplyType.E, 1,
        "EXEC_ERROR", "Command execution failed")
    
    # Database denials
    EN_DB_D_001 = ReplyCode("EN-DB-D-001", Layer.EN, Area.DB, ReplyType.D, 1,
        "DB_OP_DENIED", "Database operation denied by policy")
    
    # Contract/scope enforcement
    EN_OPEN_S_001 = ReplyCode("EN-OPEN-S-001", Layer.EN, Area.OPEN, ReplyType.S, 1,
        "CONTRACT_SCOPE_OK", "Operation within contract scope")
    EN_OPEN_D_002 = ReplyCode("EN-OPEN-D-002", Layer.EN, Area.OPEN, ReplyType.D, 2,
        "OUTSIDE_CONTRACT_SCOPE", "Operation outside contract scope")
    EN_OPEN_E_001 = ReplyCode("EN-OPEN-E-001", Layer.EN, Area.OPEN, ReplyType.E, 1,
        "SCOPE_CHECK_ERROR", "Unexpected error checking contract scope")
    
    # Policy validation
    EN_VAL_S_001 = ReplyCode("EN-VAL-S-001", Layer.EN, Area.VAL, ReplyType.S, 1,
        "POLICY_HEALTHY", "Policy system healthy")
    EN_VAL_E_001 = ReplyCode("EN-VAL-E-001", Layer.EN, Area.VAL, ReplyType.E, 1,
        "POLICY_ERROR", "Policy system error")
    
    # =========================================================================
    # CT (Contract System) - Contract lifecycle only
    # May emit: S, I, E    Must NOT emit: D
    # =========================================================================
    
    # Open
    CT_OPEN_S_001 = ReplyCode("CT-OPEN-S-001", Layer.CT, Area.OPEN, ReplyType.S, 1,
        "CONTRACT_OPENED", "Contract opened")
    CT_OPEN_I_001 = ReplyCode("CT-OPEN-I-001", Layer.CT, Area.OPEN, ReplyType.I, 1,
        "CONTRACT_MALFORMED", "Contract request malformed")
    CT_OPEN_I_002 = ReplyCode("CT-OPEN-I-002", Layer.CT, Area.OPEN, ReplyType.I, 2,
        "CONTRACT_ALREADY_ACTIVE", "A contract is already active")
    CT_OPEN_E_001 = ReplyCode("CT-OPEN-E-001", Layer.CT, Area.OPEN, ReplyType.E, 1,
        "CONTRACT_OPEN_ERROR", "Unexpected error opening contract")
    
    # Close
    CT_CLOSE_S_001 = ReplyCode("CT-CLOSE-S-001", Layer.CT, Area.CLOSE, ReplyType.S, 1,
        "CONTRACT_CLOSED", "Contract closed")
    CT_CLOSE_S_002 = ReplyCode("CT-CLOSE-S-002", Layer.CT, Area.CLOSE, ReplyType.S, 2,
        "CONTRACT_CANCELLED", "Contract cancelled")
    CT_CLOSE_I_001 = ReplyCode("CT-CLOSE-I-001", Layer.CT, Area.CLOSE, ReplyType.I, 1,
        "NO_ACTIVE_CONTRACT", "No active contract to close")
    CT_CLOSE_E_001 = ReplyCode("CT-CLOSE-E-001", Layer.CT, Area.CLOSE, ReplyType.E, 1,
        "CONTRACT_CLOSE_ERROR", "Unexpected error closing contract")
    
    # Validation
    CT_VAL_S_001 = ReplyCode("CT-VAL-S-001", Layer.CT, Area.VAL, ReplyType.S, 1,
        "CONTRACT_VALID", "Contract validation passed")
    CT_VAL_I_001 = ReplyCode("CT-VAL-I-001", Layer.CT, Area.VAL, ReplyType.I, 1,
        "CONTRACT_INVALID", "Contract validation failed")
    CT_VAL_I_002 = ReplyCode("CT-VAL-I-002", Layer.CT, Area.VAL, ReplyType.I, 2,
        "MODE_NOT_INITIALIZED", "Agent mode not initialized — call ck3_get_mode_instructions() first")
    CT_VAL_E_001 = ReplyCode("CT-VAL-E-001", Layer.CT, Area.VAL, ReplyType.E, 1,
        "CONTRACT_VAL_ERROR", "Unexpected error validating contract")
    
    # Script Signing
    CT_SIGN_S_001 = ReplyCode("CT-SIGN-S-001", Layer.CT, Area.SIGN, ReplyType.S, 1,
        "SCRIPT_SIGNED", "Script approved and signed")
    CT_SIGN_I_002 = ReplyCode("CT-SIGN-I-002", Layer.CT, Area.SIGN, ReplyType.I, 2,
        "SCRIPT_NOT_IN_WIP", "Script must be under root:ck3raven_data/wip/")
    CT_SIGN_I_003 = ReplyCode("CT-SIGN-I-003", Layer.CT, Area.SIGN, ReplyType.I, 3,
        "SCRIPT_REJECTED", "User rejected the script")
    CT_SIGN_I_004 = ReplyCode("CT-SIGN-I-004", Layer.CT, Area.SIGN, ReplyType.I, 4,
        "SCRIPT_SIGN_TIMEOUT", "Script sign timed out — user did not respond")
    CT_SIGN_I_005 = ReplyCode("CT-SIGN-I-005", Layer.CT, Area.SIGN, ReplyType.I, 5,
        "SCRIPT_PATH_REQUIRED", "script_path is required for sign_script/review_script")
    CT_SIGN_E_001 = ReplyCode("CT-SIGN-E-001", Layer.CT, Area.SIGN, ReplyType.E, 1,
        "SCRIPT_READ_ERROR", "Failed to read script content")
    CT_SIGN_E_002 = ReplyCode("CT-SIGN-E-002", Layer.CT, Area.SIGN, ReplyType.E, 2,
        "SCRIPT_IPC_ERROR", "IPC error during script signing")
    CT_SIGN_E_003 = ReplyCode("CT-SIGN-E-003", Layer.CT, Area.SIGN, ReplyType.E, 3,
        "SCRIPT_OPEN_FAILED", "VS Code failed to open script for review")

    # Review
    CT_SIGN_S_002 = ReplyCode("CT-SIGN-S-002", Layer.CT, Area.SIGN, ReplyType.S, 2,
        "SCRIPT_REVIEW_OPENED", "Script opened in VS Code for review")
    
    # =========================================================================
    # MCP (Infrastructure / tool-owned outcomes)
    # May emit: E, I, S    Must NOT emit: D
    # =========================================================================
    
    MCP_SYS_E_001 = ReplyCode("MCP-SYS-E-001", Layer.MCP, Area.SYS, ReplyType.E, 1,
        "SYS_CRASH", "Unexpected exception")
    MCP_SYS_E_002 = ReplyCode("MCP-SYS-E-002", Layer.MCP, Area.SYS, ReplyType.E, 2,
        "SERIALIZATION_ERROR", "Response not JSON-serializable")
    MCP_SYS_E_003 = ReplyCode("MCP-SYS-E-003", Layer.MCP, Area.SYS, ReplyType.E, 3,
        "RESPONSE_TOO_LARGE", "Response exceeds size limit")
    MCP_SYS_I_001 = ReplyCode("MCP-SYS-I-001", Layer.MCP, Area.SYS, ReplyType.I, 1,
        "INPUT_INVALID", "Tool input validation failed")
    MCP_SYS_I_002 = ReplyCode("MCP-SYS-I-002", Layer.MCP, Area.SYS, ReplyType.I, 2,
        "COMMAND_UNKNOWN", "Unknown command for tool")
    MCP_SYS_I_003 = ReplyCode("MCP-SYS-I-003", Layer.MCP, Area.SYS, ReplyType.I, 3,
        "SCRIPT_PATH_INVALID", "Script path must be canonical session path (root:/mod:) or relative to working_dir (default root:ck3raven_data/wip), and remain under working_dir")
    
    # MCP Success codes (for tool-owned success outcomes that do not belong to WA, EN, or CT)
    MCP_SYS_S_001 = ReplyCode("MCP-SYS-S-001", Layer.MCP, Area.SYS, ReplyType.S, 1,
        "SYS_OP_OK", "System-owned operation complete")
    MCP_SYS_S_002 = ReplyCode("MCP-SYS-S-002", Layer.MCP, Area.SYS, ReplyType.S, 2,
        "SYS_PREVIEW_OK", "System-owned preview complete")
    MCP_SYS_S_003 = ReplyCode("MCP-SYS-S-003", Layer.MCP, Area.SYS, ReplyType.S, 3,
        "SYS_SPILL_OK", "Result spilled to file (context budget exceeded)")
    
    # MCP DB codes (for system-owned database operations)
    MCP_DB_S_001 = ReplyCode("MCP-DB-S-001", Layer.MCP, Area.DB, ReplyType.S, 1,
        "DB_OP_OK", "System-owned DB operation complete")
    MCP_DB_S_002 = ReplyCode("MCP-DB-S-002", Layer.MCP, Area.DB, ReplyType.S, 2,
        "DB_PREVIEW_OK", "System-owned DB preview complete")

    # MCP validation codes (tool-owned validation outcomes)
    MCP_VAL_S_001 = ReplyCode("MCP-VAL-S-001", Layer.MCP, Area.VAL, ReplyType.S, 1,
        "REFERENCE_FOUND", "Reference validation complete: symbol found")
    MCP_VAL_S_002 = ReplyCode("MCP-VAL-S-002", Layer.MCP, Area.VAL, ReplyType.S, 2,
        "REFERENCE_NOT_FOUND", "Reference validation complete: symbol not found")
    MCP_VAL_E_001 = ReplyCode("MCP-VAL-E-001", Layer.MCP, Area.VAL, ReplyType.E, 1,
        "REFERENCE_LOOKUP_ERROR", "Reference validation failed due to a database error")

    # =========================================================================
    # MCP-QB (QBuilder daemon / build system)
    # =========================================================================

    # Status
    MCP_QB_S_001 = ReplyCode("MCP-QB-S-001", Layer.MCP, Area.QB, ReplyType.S, 1,
        "DAEMON_STATUS", "Daemon status retrieved")
    MCP_QB_S_002 = ReplyCode("MCP-QB-S-002", Layer.MCP, Area.QB, ReplyType.S, 2,
        "DAEMON_STARTED", "Daemon started")
    MCP_QB_S_003 = ReplyCode("MCP-QB-S-003", Layer.MCP, Area.QB, ReplyType.S, 3,
        "DAEMON_STOPPED", "Daemon shutdown complete")
    MCP_QB_S_004 = ReplyCode("MCP-QB-S-004", Layer.MCP, Area.QB, ReplyType.S, 4,
        "DAEMON_RESTARTED", "Daemon restarted with fresh process")
    MCP_QB_S_005 = ReplyCode("MCP-QB-S-005", Layer.MCP, Area.QB, ReplyType.S, 5,
        "DISCOVERY_ENQUEUED", "Discovery tasks enqueued for active playset")
    MCP_QB_S_006 = ReplyCode("MCP-QB-S-006", Layer.MCP, Area.QB, ReplyType.S, 6,
        "FILES_ENQUEUED", "Files enqueued for processing")
    MCP_QB_S_007 = ReplyCode("MCP-QB-S-007", Layer.MCP, Area.QB, ReplyType.S, 7,
        "QUEUE_IDLE", "Daemon queue is idle — all work complete")
    MCP_QB_S_008 = ReplyCode("MCP-QB-S-008", Layer.MCP, Area.QB, ReplyType.S, 8,
        "LOGS_RETRIEVED", "Daemon log output retrieved")
    MCP_QB_S_009 = ReplyCode("MCP-QB-S-009", Layer.MCP, Area.QB, ReplyType.S, 9,
        "FAILED_ITEMS", "Failed queue items retrieved")
    MCP_QB_S_010 = ReplyCode("MCP-QB-S-010", Layer.MCP, Area.QB, ReplyType.S, 10,
        "FAILED_RETRIED", "Failed items re-enqueued for retry")

    # Invalid
    MCP_QB_I_001 = ReplyCode("MCP-QB-I-001", Layer.MCP, Area.QB, ReplyType.I, 1,
        "DAEMON_NOT_RUNNING", "Daemon is not running")
    MCP_QB_I_002 = ReplyCode("MCP-QB-I-002", Layer.MCP, Area.QB, ReplyType.I, 2,
        "DAEMON_ALREADY_RUNNING", "Daemon is already running")
    MCP_QB_I_003 = ReplyCode("MCP-QB-I-003", Layer.MCP, Area.QB, ReplyType.I, 3,
        "AWAIT_TIMEOUT", "Timed out waiting for daemon to become idle")
    MCP_QB_I_004 = ReplyCode("MCP-QB-I-004", Layer.MCP, Area.QB, ReplyType.I, 4,
        "ENQUEUE_NO_PATHS", "No paths provided for enqueue command")
    MCP_QB_I_005 = ReplyCode("MCP-QB-I-005", Layer.MCP, Area.QB, ReplyType.I, 5,
        "LOGS_NOT_FOUND", "No daemon log file found for today")
    MCP_QB_I_006 = ReplyCode("MCP-QB-I-006", Layer.MCP, Area.QB, ReplyType.I, 6,
        "NO_FAILED_ITEMS", "No failed items in queue")
    MCP_QB_I_007 = ReplyCode("MCP-QB-I-007", Layer.MCP, Area.QB, ReplyType.I, 7,
        "LOCK_HELD_NO_IPC", "Writer lock held but daemon IPC not responding")

    # Error
    MCP_QB_E_001 = ReplyCode("MCP-QB-E-001", Layer.MCP, Area.QB, ReplyType.E, 1,
        "DAEMON_START_FAILED", "Failed to start daemon subprocess")
    MCP_QB_E_002 = ReplyCode("MCP-QB-E-002", Layer.MCP, Area.QB, ReplyType.E, 2,
        "DAEMON_IPC_ERROR", "Daemon IPC communication error")
    MCP_QB_E_003 = ReplyCode("MCP-QB-E-003", Layer.MCP, Area.QB, ReplyType.E, 3,
        "RESTART_FAILED", "Daemon restart failed — stopped but could not start")
    MCP_QB_E_004 = ReplyCode("MCP-QB-E-004", Layer.MCP, Area.QB, ReplyType.E, 4,
        "LOG_READ_ERROR", "Error reading daemon log file")


# Build lookup dict for code validation
_ALL_CODES: dict[str, ReplyCode] = {}
for name in dir(Codes):
    if name.startswith("_"):
        continue
    val = getattr(Codes, name)
    if isinstance(val, ReplyCode):
        if val.code in _ALL_CODES:
            raise ValueError(f"Duplicate code: {val.code}")
        _ALL_CODES[val.code] = val


# Code format regex
_CODE_PATTERN = re.compile(r"^(WA|EN|CT|MCP)-([A-Z]+)-([SIDE])-(\d{3})$")


def validate_code_format(code: str) -> tuple[bool, str]:
    """
    Validate a code string against canonical format.
    
    Returns (valid, error_message).
    """
    match = _CODE_PATTERN.match(code)
    if not match:
        return False, f"Code {code!r} does not match format LAYER-AREA-TYPE-NNN"
    
    layer_str, area_str, type_str, num_str = match.groups()
    
    # Validate layer
    try:
        layer = Layer(layer_str)
    except ValueError:
        return False, f"Unknown layer: {layer_str}"
    
    # Validate area
    try:
        area = Area(area_str)
    except ValueError:
        return False, f"Unknown area: {area_str}"
    
    # Validate type
    try:
        reply_type = ReplyType(type_str)
    except ValueError:
        return False, f"Unknown type: {type_str}"
    
    # Validate layer ownership
    if reply_type not in LAYER_ALLOWED_TYPES[layer]:
        allowed = [t.value for t in LAYER_ALLOWED_TYPES[layer]]
        return False, f"Layer {layer.value} cannot emit type {type_str}. Allowed: {allowed}"
    
    return True, ""


def get_code(code: str) -> ReplyCode | None:
    """Look up a registered code."""
    return _ALL_CODES.get(code)


def get_message(code: str, **params) -> str:
    """Get a human-readable message for a registered code.

    Uses the ReplyCode.description field from the canonical registry.
    Supports template interpolation: placeholders like {tool}, {command}
    in the description are replaced with values from **params.

    Falls back to 'Unknown code: <code>' if the code is not registered.
    Falls back to raw description if template params are missing.
    """
    rc = _ALL_CODES.get(code)
    if rc is None:
        return f"Unknown code: {code}"
    try:
        return rc.description.format(**params)
    except KeyError:
        # Missing template params — return raw description
        return rc.description


def validate_registry() -> list[str]:
    """
    Validate the entire registry at startup.
    
    Returns list of errors (empty = valid).
    """
    errors = []
    
    for code_str, code_obj in _ALL_CODES.items():
        valid, err = validate_code_format(code_str)
        if not valid:
            errors.append(err)
    
    # Check for forbidden patterns
    for code_str, code_obj in _ALL_CODES.items():
        if code_obj.layer == Layer.CT and code_obj.reply_type == ReplyType.D:
            errors.append(f"FORBIDDEN: CT layer emitting D: {code_str}")
        if code_obj.layer == Layer.WA and code_obj.reply_type == ReplyType.D:
            errors.append(f"FORBIDDEN: WA layer emitting D: {code_str}")
    
    return errors


# Run validation at import time
_STARTUP_ERRORS = validate_registry()
if _STARTUP_ERRORS:
    raise RuntimeError(f"Reply code registry validation failed:\n" + "\n".join(_STARTUP_ERRORS))


# =============================================================================
# Translation table: Legacy code -> Canonical code
# =============================================================================

LEGACY_TO_CANONICAL: dict[str, str] = {
    # MCP-SYS-S-900 (legacy wrap) -> domain-specific success codes
    # These need context-aware replacement, listed here for reference
    
    # DB-CONN-*
    "DB-CONN-S-001": "WA-DB-S-001",
    "DB-CONN-E-001": "MCP-SYS-E-001",
    
    # FILE-OP-*
    "FILE-OP-S-001": "WA-WRITE-S-001",  # or WA-READ-S-001 depending on operation
    "FILE-OP-D-001": "EN-WRITE-D-001",
    "FILE-OP-I-001": "WA-RES-I-001",
    "FILE-OP-E-001": "MCP-SYS-E-001",
    
    # FOLDER-OP-*
    "FOLDER-OP-S-001": "WA-READ-S-001",
    "FOLDER-OP-E-001": "MCP-SYS-E-001",
    
    # PLAYSET-OP-*
    "PLAYSET-OP-S-001": "WA-VIS-S-001",
    "PLAYSET-OP-S-002": "WA-VIS-S-001",
    "PLAYSET-OP-E-001": "MCP-SYS-E-001",
    
    
    # REPAIR-OP-*
    "REPAIR-OP-S-001": "WA-SYS-S-001",
    "REPAIR-OP-I-001": "WA-SYS-S-002",  # dry run is success, not invalid
    "REPAIR-OP-E-001": "MCP-SYS-E-001",
    
    # CONTRACT-OP-*
    "CONTRACT-OP-S-001": "CT-OPEN-S-001",
    "CONTRACT-OP-S-002": "CT-CLOSE-S-001",
    "CONTRACT-OP-S-003": "CT-VAL-S-001",
    "CONTRACT-OP-S-004": "CT-CLOSE-S-002",
    "CONTRACT-OP-I-001": "CT-CLOSE-I-001",  # "no active contract" as info, not governance
    "CONTRACT-OP-D-001": "EN-WRITE-D-002",   # CRITICAL: governance denial -> EN
    "CONTRACT-OP-E-001": "MCP-SYS-E-001",
    
    # SEARCH-OP-*
    "SEARCH-OP-I-001": "WA-RES-I-001",  # path not found
    
    # PARSE-AST-*
    "PARSE-AST-S-001": "WA-PARSE-S-001",
    "PARSE-AST-I-001": "WA-PARSE-I-001",
    "PARSE-AST-E-001": "MCP-SYS-E-001",
    
    # JRN-*
    "JRN-S-001": "WA-LOG-S-001",
    "JRN-E-001": "MCP-SYS-E-001",
    
    # DB-QUERY-*
    "DB-QUERY-I-001": "WA-DB-I-001",
    "DB-QUERY-I-002": "WA-DB-I-002",
    
    # Agent's Feb 3 mistakes
    "CONFLICTS-I-001": "WA-SYS-I-001",   # unknown command
    "BRIEFING-I-001": "WA-VIS-I-001",    # no playset
    "DBQUERY-I-001": "WA-DB-I-001",      # no SQL
    "QBUILDER-I-001": "WA-SYS-I-001",    # unknown command
    
    # MCP-SYS-* remappings
    "MCP-SYS-S-900": "WA-SYS-S-001",    # legacy wrap -> generic WA success (context-dependent)
    "MCP-SYS-I-901": "WA-CFG-I-001",    # deprecated feature
    "MCP-SYS-D-903": "EN-DB-D-001",     # policy denial -> EN
}
