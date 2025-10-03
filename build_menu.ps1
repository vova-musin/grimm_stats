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
$cmd = ".\\build.ps1 -Version `"$version`" -PublishRelease -TagPrefix v"
Write-Host "Running: $cmd" -ForegroundColor DarkGray
& powershell -NoProfile -ExecutionPolicy Bypass -Command $cmd
Write-Host "Done." -ForegroundColor Green
