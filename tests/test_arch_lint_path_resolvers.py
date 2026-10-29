from pathlib import Path

from tools.arch_lint.config import LintConfig
from tools.arch_lint.rules import check_internal_path_resolvers
from tools.arch_lint.scanner import SourceFile


def _source(tmp_path: Path, name: str, text: str) -> SourceFile:
    path = tmp_path / name
    return SourceFile(path=path, text=text, lines=text.splitlines())


def test_rejects_python_candidate_path_loop(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.py",
        """def _find_python_path():
    candidates = [Path('.venv/python'), Path('venv/python')]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError('missing')
""",
    )

    findings = check_internal_path_resolvers(LintConfig(root=tmp_path), source)

    assert [finding.rule_id for finding in findings] == ["PATH-FALLBACK-01"]


def test_rejects_python_branch_fallback(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "logs.py",
        """def _get_log_path():
    if structured_log.exists():
        return structured_log
    return provider_log
""",
    )

    findings = check_internal_path_resolvers(LintConfig(root=tmp_path), source)

    assert [finding.rule_id for finding in findings] == ["PATH-FALLBACK-01"]


def test_accepts_single_configured_path_validation(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.py",
        """def _get_repo_path():
    configured = Path(ROOT_REPO)
    if not configured.exists():
        raise RuntimeError('missing ROOT_REPO')
    return configured
""",
    )

    assert check_internal_path_resolvers(LintConfig(root=tmp_path), source) == []


def test_accepts_optional_python_configured_path(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.py",
        """def _get_repo_path():
    if configured.exists():
        return configured
    return None
""",
    )

    assert check_internal_path_resolvers(LintConfig(root=tmp_path), source) == []


def test_world_adapter_owns_resolution(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "world_adapter.py",
        """def _resolve_path():
    for candidate in candidates:
        if candidate.exists():
            return candidate
""",
    )

    assert check_internal_path_resolvers(LintConfig(root=tmp_path), source) == []


def test_rejects_typescript_candidate_path_loop(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.ts",
        """function findPythonPath(): string {
    const candidates = [workspacePython, homePython];
    for (const candidate of candidates) {
        if (existsSync(candidate)) {
            return candidate;
        }
    }
    throw new Error('missing');
}
""",
    )

    findings = check_internal_path_resolvers(LintConfig(root=tmp_path), source)

    assert [finding.rule_id for finding in findings] == ["PATH-FALLBACK-01"]


def test_rejects_typescript_branch_fallback(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.ts",
        """function getRepoPath(): string {
    if (existsSync(primaryRepo)) {
        return primaryRepo;
    }
    return fallbackRepo;
}
""",
    )

    findings = check_internal_path_resolvers(LintConfig(root=tmp_path), source)

    assert [finding.rule_id for finding in findings] == ["PATH-FALLBACK-01"]


def test_accepts_typescript_configured_path_validation(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        "provider.ts",
        """function getRepoPath(): string {
    if (!existsSync(ROOT_REPO)) {
        throw new Error('missing ROOT_REPO');
    }
    return ROOT_REPO;
}
""",
    )

    assert check_internal_path_resolvers(LintConfig(root=tmp_path), source) == []


def test_live_mcp_and_explorer_have_no_internal_path_fallbacks() -> None:
    root = Path(__file__).parents[1]
    paths = [
        *root.joinpath("tools", "ck3lens_mcp").rglob("*.py"),
        *root.joinpath("tools", "ck3lens-explorer", "src").rglob("*.ts"),
    ]
    findings = []
    config = LintConfig(root=root)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        source = SourceFile(path=path, text=text, lines=text.splitlines())
        findings.extend(check_internal_path_resolvers(config, source))

    assert findings == [], [
        f"{finding.path}:{finding.line} {finding.symbol}" for finding in findings
    ]
