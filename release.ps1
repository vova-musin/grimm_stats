param(
	[string]$Version = "1.0.6"
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "build.ps1"
if (-not (Test-Path $script)) { throw "build.ps1 not found" }

# Настройки репозитория
$Owner = "vova-musin"
$Repo = "grimm_stats"
$TagPrefix = "v"

# 1) Сборка (автоинкремент либо заданная версия X.Y.Z)
& powershell -NoProfile -ExecutionPolicy Bypass -File $script -Version $Version | Out-Host

# 2) Читаем манифест (semver)
if (-not (Test-Path "version.json")) { throw "version.json not found" }
$vj = Get-Content version.json | ConvertFrom-Json
$semver = $vj.semver
if (-not $semver -and $vj.version) {
	$ver = [int]$vj.version
	$semver = "{0}.{1}.{2}" -f ([math]::Floor($ver/100)), ([math]::Floor(($ver % 100)/10)), ($ver % 10)
}
if (-not $semver) { throw "semver not found in version.json" }
$tag = "$TagPrefix$semver"

# 3) Токен GitHub (без организации, обычный PAT): repo scope достаточен
$token = $env:GITHUB_TOKEN
if (-not $token) { throw "Set GITHUB_TOKEN environment variable with repo scope." }
$base = "https://api.github.com"
$headers = @{ Authorization = "token $token"; "User-Agent" = "grimm-stats-release" }

# 4) Создаём релиз (если уже есть — берём существующий)
try {
	$body = @{ tag_name = $tag; name = $tag; body = "Автосборка $tag"; draft = $false; prerelease = $false } | ConvertTo-Json
	$rel = Invoke-RestMethod -Method Post -Uri "$base/repos/$Owner/$Repo/releases" -Headers $headers -Body $body -ContentType "application/json"
} catch {
	# Если 422 (уже существует) — достанем по тегу
	try {
		$rel = Invoke-RestMethod -Method Get -Uri "$base/repos/$Owner/$Repo/releases/tags/$tag" -Headers $headers
	} catch {
		throw $_
	}
}
if (-not $rel) { throw "Cannot get or create release $tag" }

# 5) Загружаем ассет GrimmStats.exe
$assetPath = Join-Path $PSScriptRoot "dist/GrimmStats.exe"
if (-not (Test-Path $assetPath)) { throw "dist/GrimmStats.exe not found" }

# Удалим существующий ассет с тем же именем, если есть
try {
	$assets = Invoke-RestMethod -Method Get -Uri "$base/repos/$Owner/$Repo/releases/$($rel.id)/assets" -Headers $headers
	$existing = $assets | Where-Object { $_.name -eq 'GrimmStats.exe' }
	if ($existing) {
		Invoke-RestMethod -Method Delete -Uri "$base/repos/$Owner/$Repo/releases/assets/$($existing.id)" -Headers $headers | Out-Null
	}
} catch { }

$uploadUrl = "https://uploads.github.com/repos/$Owner/$Repo/releases/$($rel.id)/assets?name=GrimmStats.exe"
$uploadHeaders = @{ Authorization = "token $token"; "Content-Type" = "application/octet-stream"; "User-Agent" = "grimm-stats-release" }
Invoke-RestMethod -Method Post -Uri $uploadUrl -Headers $uploadHeaders -InFile $assetPath -ContentType "application/octet-stream" | Out-Null

Write-Host "Release published: https://github.com/$Owner/$Repo/releases/tag/$tag" -ForegroundColor Green
