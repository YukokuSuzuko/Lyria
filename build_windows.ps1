param([string]$PythonExe = 'python', [string]$OutputDirectory = '..\dist\Lyria_0.11.0.dev2')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & $PythonExe -m venv (Join-Path $PSScriptRoot '.build-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed.' }
}
# CPU and DirectML wheels share the onnxruntime import directory. Remove the old
# CPU distribution before upgrading an existing build environment.
& $buildPython -m pip uninstall -y onnxruntime
& $buildPython -m pip install --force-reinstall -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $buildPython -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
& $buildPython -m PyInstaller --noconfirm --distpath $OutputDirectory --workpath .build Lyria.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
$portableRoot = Join-Path $OutputDirectory 'Lyria'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'examples') -Destination $portableRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'licenses') -Destination $portableRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY.md') -Destination $portableRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination $portableRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs') -Destination $portableRoot -Recurse -Force
$portableRoot = (Resolve-Path -LiteralPath $portableRoot).Path
$check = Join-Path $portableRoot 'selfcheck.json'
$app = Join-Path $portableRoot 'Lyria.exe'
$process = Start-Process -FilePath $app -ArgumentList @('--self-check',('"' + $check + '"')) -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $check)) { throw 'Portable self-check failed.' }
if (-not ((Get-Content -LiteralPath $check -Raw | ConvertFrom-Json).ok)) { throw 'Portable self-check reported failure.' }
Write-Output ('Portable application: ' + $app)
