# Builds the standalone Windows installer.
#
# Requires: a python.org CPython 3.10+ (NOT the Microsoft Store build - its
# sandboxed Tcl/Tk breaks freezing) and Inno Setup 6 (winget install
# JRSoftware.InnoSetup). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
#
# Output: build\dist\mtd-classifier-setup-<version>.exe

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

# 1. Build venv (python.org build; py -3 resolves the default CPython)
if (-not (Test-Path "build\venv\Scripts\python.exe")) {
    py -3 -m venv build\venv
}
& build\venv\Scripts\python.exe -m pip install --quiet --upgrade pip
# clean reinstall so files removed from the package can't linger in the
# bundle - including setuptools' stale build\lib tree, which silently
# re-packages deleted files into every wheel built from it
Remove-Item -Recurse -Force build\lib, src\mtd_classifier.egg-info -ErrorAction SilentlyContinue
& build\venv\Scripts\pip.exe uninstall --quiet -y mtd-classifier 2>$null
& build\venv\Scripts\pip.exe install --quiet . pillow pyinstaller

# 2. Freeze the app (one-dir, windowed). --clean and a fresh dist dir stop
#    stale files from previous builds surviving in the bundle.
Remove-Item -Recurse -Force build\dist\mtd-classifier -ErrorAction SilentlyContinue
& build\venv\Scripts\pyinstaller.exe --clean --distpath build\dist --workpath build\work -y installer\mtd_classifier.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 3. Compile the installer
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found - winget install JRSoftware.InnoSetup" }
& $iscc /Q "installer\mtd-classifier.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

Get-Item build\dist\mtd-classifier-setup-*.exe
