param(
	[string]$Python = "python",
	[string]$Name = "GrimmStats",
	[string]$CopyTo = "C:\My Drive\Grimm",
	[switch]$AutoRelease = $false,
	[string]$CommitMessage = "Auto build"
)

$ErrorActionPreference = "Stop"

Write-Host "[1/6] Create virtual env .venv" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
	& $Python -m venv .venv
}

$venvPython = Join-Path ".venv" "Scripts/python.exe"
$venvPip = Join-Path ".venv" "Scripts/pip.exe"

Write-Host "[2/6] Upgrade pip" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip

Write-Host "[3/6] Install requirements" -ForegroundColor Cyan
& $venvPip install -r requirements.txt
& $venvPip install pillow | Out-Null

Write-Host "[4/6] Prepare icon (PNG -> ICO if needed)" -ForegroundColor Cyan
$pngCandidates = @("icon.png", "photo_2025-09-21_18-08-53.png")
$pngPath = $null
foreach ($p in $pngCandidates) { if (Test-Path $p) { $pngPath = $p; break } }
if (-not (Test-Path "icon.ico") -and $pngPath) {
	try {
		Write-Host "Converting $pngPath -> icon.ico" -ForegroundColor Cyan
		$py = @"
from PIL import Image
import sys
src = sys.argv[1]
im = Image.open(src).convert("RGBA")
sizes = [(256,256),(128,128),(64,64),(32,32),(16,16)]
im.save("icon.ico", sizes=sizes)
"@
		& $venvPython -c $py $pngPath
	} catch {
		Write-Warning "Не удалось сконвертировать PNG в ICO. Продолжаю без иконки окна."
	}
}

Write-Host "[5/7] Update version in manifest" -ForegroundColor Cyan
# Читаем текущую версию и инкрементируем
$versionFile = "version.json"
$version = 1
$buildDate = Get-Date -Format "yyyy-MM-dd"
if (Test-Path $versionFile) {
	try {
		$versionData = Get-Content $versionFile | ConvertFrom-Json
		$version = [int]$versionData.version + 1
		Write-Host "Incrementing version: $($versionData.version) -> $version" -ForegroundColor Yellow
	} catch {
		Write-Warning "Failed to read version, using version 1"
	}
}

# Конвертируем integer версию в semver (X.Y.Z)
$major = [math]::Floor($version / 100)
$minor = [math]::Floor(($version % 100) / 10)
$patch = $version % 10
$semver = "$major.$minor.$patch"

# Обновляем манифест
$manifest = @{
	version = $version
	semver = $semver
	build_date = $buildDate
    exe_url = "https://github.com/vova-musin/grimm_stats/releases/download/v$semver/GrimmStats.exe"
    exe_file_id = ""
    manifest_file_id = ""
	changelog = @("Version $version ($semver) - auto build from $buildDate")
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content $versionFile -Encoding UTF8

Write-Host "[6/7] Clean previous builds" -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build,dist
Remove-Item -Force -ErrorAction SilentlyContinue *.spec

# Формируем аргументы PyInstaller корректно
$args = @('--noconfirm','--clean','--onefile','--windowed','--name', $Name)
if (Test-Path "icon.ico") {
	$args += @('--icon','icon.ico')
	$args += @('--add-data','icon.ico;.')
} else {
	Write-Host "(опционально) Положите icon.ico или icon.png в корень проекта, чтобы задать иконку." -ForegroundColor Yellow
}
if (Test-Path $versionFile) {
    # Вкладываем version.json внутрь onefile, чтобы приложение могло читать локальную версию из _MEIPASS
    $args += @('--add-data',"$versionFile;.")
}

Write-Host "[7/7] Build with PyInstaller" -ForegroundColor Cyan
& $venvPython -m PyInstaller @args main.py

# Собираем updater.exe (onefile, console)
Write-Host "[post] Build updater" -ForegroundColor Cyan
& $venvPython -m PyInstaller --noconfirm --clean --onefile --name updater updater.py | Out-Null

# Доп. шаг: копирование результата в целевую папку (если задано)
if ($CopyTo) {
	if (-not (Test-Path $CopyTo)) {
		Write-Host "[post] Создаю папку назначения: $CopyTo" -ForegroundColor Cyan
		New-Item -ItemType Directory -Force -Path $CopyTo | Out-Null
	}
	$dstExe = Join-Path $CopyTo ("{0}.exe" -f $Name)
	if (Test-Path "dist/$Name.exe") {
		Write-Host "[post] Copying dist/$Name.exe -> $dstExe" -ForegroundColor Cyan
		Copy-Item -Force "dist/$Name.exe" $dstExe
	}
	if (Test-Path "dist/updater.exe") {
		$dstUpd = Join-Path $CopyTo "updater.exe"
		Write-Host "[post] Copying dist/updater.exe -> $dstUpd" -ForegroundColor Cyan
		Copy-Item -Force "dist/updater.exe" $dstUpd
	}
	if (Test-Path $versionFile) {
		$dstVer = Join-Path $CopyTo "version.json"
		Write-Host "[post] Copying $versionFile -> $dstVer" -ForegroundColor Cyan
		Copy-Item -Force $versionFile $dstVer
	}
}

Write-Host "Done. EXE: dist/$Name.exe" -ForegroundColor Green

# Автоматический релиз на GitHub
if ($AutoRelease) {
	Write-Host "`n[GitHub] Auto-release to GitHub" -ForegroundColor Cyan
	
	# Проверяем, что git настроен
	$gitStatus = git status 2>&1
	if ($LASTEXITCODE -ne 0) {
		Write-Warning "Git not initialized or error. Skipping auto-release."
		exit 0
	}
	
	Write-Host "[GitHub] Adding files..." -ForegroundColor Yellow
	git add version.json
	git add build.ps1
	
	Write-Host "[GitHub] Committing changes..." -ForegroundColor Yellow
	git commit -m "$CommitMessage - v$semver" 2>&1 | Out-Null
	if ($LASTEXITCODE -ne 0) {
		Write-Host "No changes to commit or commit failed" -ForegroundColor Gray
	}
	
	Write-Host "[GitHub] Creating tag v$semver..." -ForegroundColor Yellow
	# Удаляем тег если существует (для перезаписи)
	git tag -d "v$semver" 2>&1 | Out-Null
	$LASTEXITCODE = 0  # Сбрасываем код ошибки
	git tag "v$semver"
	
	Write-Host "[GitHub] Pushing to GitHub..." -ForegroundColor Yellow
	$pushOutput = git push origin main 2>&1
	if ($LASTEXITCODE -ne 0 -and $pushOutput -notmatch "Everything up-to-date") {
		Write-Warning "Failed to push main branch: $pushOutput"
	}
	
	Write-Host "[GitHub] Pushing tag v$semver..." -ForegroundColor Yellow
	$tagOutput = git push origin "v$semver" --force 2>&1
	
	if ($LASTEXITCODE -eq 0 -or $tagOutput -match "new tag") {
		Write-Host "`nSuccessfully pushed v$semver to GitHub!" -ForegroundColor Green
		Write-Host "GitHub Actions will build and create release at:" -ForegroundColor Cyan
		Write-Host "  https://github.com/vova-musin/grimm_stats/releases/tag/v$semver" -ForegroundColor White
		Write-Host "`nCheck workflow status at:" -ForegroundColor Cyan
		Write-Host "  https://github.com/vova-musin/grimm_stats/actions" -ForegroundColor White
	} else {
		Write-Warning "Failed to push tag to GitHub: $tagOutput"
	}
}
