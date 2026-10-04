$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "缺少虚拟环境；先运行 scripts/dev.ps1"
}
& $pythonExe -m unittest discover -s (Join-Path $projectRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw 'unittest 未通过' }
& $pythonExe -m compileall -q (Join-Path $projectRoot 'src') (Join-Path $projectRoot 'bin') (Join-Path $projectRoot 'scripts')
if ($LASTEXITCODE -ne 0) { throw '语法编译未通过' }
& $pythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖检查未通过' }
