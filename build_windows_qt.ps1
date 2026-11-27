param(
    [string]$PythonExe = 'python',
    [string]$OutputDirectory = '..\dist\Lyria_v0.3.4_Windows_x64_test',
    [switch]$RunFullDesktopTests
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($env:OS -ne 'Windows_NT') { throw 'Windows packaging must run on Windows.' }
if (Test-Path -LiteralPath (Join-Path $OutputDirectory 'Lyria')) { throw 'Refusing to overwrite an existing portable package. Choose a new output directory.' }
# Fresh evidence directory. A failed new audit must never reuse old success.
$validation = Join-Path $PSScriptRoot ('..\validation\windows-build-' + (Get-Date -Format 'yyyyMMddTHHmmssfff'))
New-Item -ItemType Directory -Path $validation -ErrorAction Stop | Out-Null
$sourceReport = Join-Path $validation 'source-inputs.json'
& $PythonExe tools\audit_portable_runtime.py source --root $PSScriptRoot --report $sourceReport
if ($LASTEXITCODE -ne 0) { throw ('Portable resource audit failed. See ' + $sourceReport) }
$buildPython = Join-Path $PSScriptRoot '.build-qt-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & $PythonExe -m venv (Join-Path $PSScriptRoot '.build-qt-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Isolated Python environment creation failed.' }
}
# Only the isolated build environment is changed. Keep CPU and DirectML wheels
# from overwriting the same onnxruntime import directory.
# Uninstall both distributions: removing the CPU wheel can also remove files
# owned by an already-installed DirectML wheel. Reinstall it from the pins.
& $buildPython -m pip uninstall -y onnxruntime onnxruntime-directml
if ($LASTEXITCODE -ne 0) { throw 'Could not clear overlapping ONNX Runtime wheels.' }
& $buildPython -m pip install -r requirements.qt.txt
if ($LASTEXITCODE -ne 0) { throw 'Qt Windows dependency installation failed.' }
& $buildPython -m pip install -r requirements.qt.tests.txt
if ($LASTEXITCODE -ne 0) { throw 'Qt test-runner installation failed.' }
& $buildPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Build dependency consistency check failed.' }
$prebuildReport = Join-Path $validation 'prebuild.json'
& $buildPython tools\audit_portable_runtime.py prebuild --root $PSScriptRoot --report $prebuildReport
if ($LASTEXITCODE -ne 0) { throw ('Windows runtime/dependency preflight failed. See ' + $prebuildReport) }
if ($RunFullDesktopTests) {
    & $buildPython -m unittest discover -s tests -p 'test_*.py' -v
} else {
    & $buildPython tools\run_headless_tests.py --json-report (Join-Path $validation 'windows-backend.json')
}
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
# The unittest aggregate does not collect pytest function tests. Run the Qt
# suites separately instead of silently losing their coverage in packaging.
$qtTestFiles = @(Get-ChildItem -LiteralPath 'tests' -Filter 'test_qt_*.py' | ForEach-Object { $_.FullName })
if ($qtTestFiles.Count -eq 0) { throw 'No Qt contract tests found.' }
& $buildPython -m pytest @qtTestFiles -q ('--junitxml=' + (Join-Path $validation 'windows-qt-contracts.xml'))
if ($LASTEXITCODE -ne 0) { throw 'Qt contract tests failed; packaging stopped.' }
& $buildPython -m PyInstaller --noconfirm --distpath $OutputDirectory --workpath .build-qt LyriaQt.spec
if ($LASTEXITCODE -ne 0) { throw 'Qt packaging failed.' }
$portableRoot = Join-Path $OutputDirectory 'Lyria'
$contract = Get-Content -LiteralPath 'tools\portable_runtime_manifest.json' -Raw | ConvertFrom-Json
foreach ($name in @($contract.external_trees) + @($contract.external_files)) {
    $destination = Join-Path $portableRoot $name
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $destination -Recurse -Force
}
$portableRoot = (Resolve-Path -LiteralPath $portableRoot).Path
Copy-Item -LiteralPath 'tools\portable_runtime_manifest.json' -Destination $portableRoot -Force
$auditReport = Join-Path $portableRoot 'portable-audit.json'
& $buildPython tools\audit_portable_runtime.py postbuild --root $portableRoot --prebuild-report $prebuildReport --report $auditReport --run-binaries
if ($LASTEXITCODE -ne 0) { throw ('Packaged runtime audit failed. See ' + $auditReport) }
$app = Join-Path $portableRoot 'Lyria.exe'
$screenshot = Join-Path $portableRoot 'startup-smoke.png'
$report = Join-Path $portableRoot 'qt-selfcheck.json'
foreach ($stale in @($screenshot, $report)) {
    if (Test-Path -LiteralPath $stale) { Remove-Item -LiteralPath $stale -Force }
}
$video = Join-Path $portableRoot 'examples\synthetic_mv.mp4'
$smokeStorage = Join-Path $validation 'smoke-storage'
$process = Start-Process -FilePath $app -ArgumentList @('--video', ('"'+$video+'"'),
    '--exit-after', '5000', '--screenshot', ('"'+$screenshot+'"'),
    '--self-check', ('"'+$report+'"'), '--storage-dir', ('"'+$smokeStorage+'"')) -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $screenshot)) {
    throw 'Packaged Qt startup/screenshot smoke failed.'
}
if (-not (Test-Path -LiteralPath $report) -or -not ((Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).ok)) {
    throw 'Packaged Qt/real decoded-frame self-check failed.'
}
Get-FileHash -Algorithm SHA256 -LiteralPath $app | Format-List
Write-Output ('Candidate only: '+$app)
Write-Output 'Startup screenshot is not frame, audio, IME, OCR or smoothness acceptance. Complete README_QT.md Windows checklist before delivery.'
