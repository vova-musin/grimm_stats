param(
	[string]$Python = "python"
)

$ErrorActionPreference = "Stop"

# 1) VENV
if (-not (Test-Path ".venv")) {
	& $Python -m venv .venv
}
$venvPython = Join-Path ".venv" "Scripts/python.exe"
$venvPip = Join-Path ".venv" "Scripts/pip.exe"

# 2) Deps
& $venvPython -m pip install --upgrade pip
& $venvPip install -r requirements.txt
& $venvPip install pyinstaller pillow | Out-Null

# 3) Clean
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build,dist
Remove-Item -Force -ErrorAction SilentlyContinue *.spec

# 4) Build app
& $venvPython -m PyInstaller --noconfirm --clean --onefile --windowed --name GrimmStats --add-data "version.json;." main.py

# 5) Build updater
& $venvPython -m PyInstaller --noconfirm --clean --onefile --name updater updater.py | Out-Null

Write-Host "Done. EXE: dist/GrimmStats.exe" -ForegroundColor Green
