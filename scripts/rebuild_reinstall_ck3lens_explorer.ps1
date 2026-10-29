param(
    [string]$RepoRoot,
    [switch]$SkipInstall,
    [switch]$SkipPackage,
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot -or [string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $scriptDir
}

$RepoRoot = (Resolve-Path $RepoRoot).Path
$ExtDir = Join-Path $RepoRoot 'tools\ck3lens-explorer'

if (-not (Test-Path (Join-Path $ExtDir 'package.json'))) {
    throw "Extension package.json not found under $ExtDir"
}

Push-Location $ExtDir
try {
    if (-not $SkipNpmInstall) {
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE"
        }
    }

    npm run compile
    if ($LASTEXITCODE -ne 0) {
        throw "npm run compile failed with exit code $LASTEXITCODE"
    }

    $vsix = $null
    if (-not $SkipPackage) {
        npm run package
        if ($LASTEXITCODE -ne 0) {
            throw "npm run package failed with exit code $LASTEXITCODE"
        }

        $vsix = Get-ChildItem -Path $ExtDir -Filter '*.vsix' |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if (-not $vsix) {
            throw 'Package step completed but no VSIX was found.'
        }
    }

    if (-not $SkipInstall) {
        if (-not $vsix) {
            $vsix = Get-ChildItem -Path $ExtDir -Filter '*.vsix' |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
        }

        if (-not $vsix) {
            throw 'Install requested but no VSIX is available. Run without -SkipPackage or place a VSIX in the extension folder.'
        }

        code --install-extension $vsix.FullName --force
        if ($LASTEXITCODE -ne 0) {
            throw "code --install-extension failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    Pop-Location
}
