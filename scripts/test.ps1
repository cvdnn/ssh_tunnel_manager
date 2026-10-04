$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "缺少虚拟环境；先运行 scripts/dev.ps1"
}
& $pythonExe (Join-Path $projectRoot 'scripts/test.py')
if ($LASTEXITCODE -ne 0) { throw '测试或依赖检查未通过' }
