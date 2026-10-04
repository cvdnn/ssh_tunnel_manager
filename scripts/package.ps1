$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "缺少虚拟环境；先运行 scripts/dev.ps1"
}
# 参数原样转发给 scripts/build_app.py，例如：.\scripts\package.ps1 --install --installer --smoke-test
& $pythonExe (Join-Path $projectRoot 'scripts/build_app.py') @args
if ($LASTEXITCODE -ne 0) { throw "打包失败，退出码 $LASTEXITCODE" }
