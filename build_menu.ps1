param()

$ErrorActionPreference = "Stop"

# Простой релиз: спрашиваем версию X.Y.Z и делаем релиз (пуш тега + сборка)

function Read-VersionSimple {
	param([string]$Default="1.0.0")
	$ans = Read-Host "Введите версию (X.Y.Z), например 1.2.3 [$Default]"
	if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
	return $ans
}

$version = Read-VersionSimple
$root = $PSScriptRoot
if (-not $root -or [string]::IsNullOrWhiteSpace($root)) { $root = (Get-Location).Path }
$scriptPath = Join-Path $root 'build.ps1'
Write-Host "Running: $scriptPath -Version $version -PublishRelease -TagPrefix v" -ForegroundColor DarkGray
& $scriptPath -Version $version -PublishRelease -TagPrefix v
Write-Host "Done." -ForegroundColor Green
