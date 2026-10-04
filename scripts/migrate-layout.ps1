param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "缺少虚拟环境；先运行 scripts/dev.ps1"
}
$migrationScript = Join-Path $PSScriptRoot 'migrate_layout.py'
if ($Apply) {
    & $pythonExe $migrationScript --apply
} else {
    & $pythonExe $migrationScript
}
if ($LASTEXITCODE -ne 0) { throw '目录迁移未完成' }
