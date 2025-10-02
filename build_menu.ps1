param()

$ErrorActionPreference = "Stop"

# Minimal interactive menu: local build toggle, app version, tag name

function Read-YesNo {
	param([string]$Prompt,[bool]$Default=$false)
	$tail = if ($Default) { "[Y/n]" } else { "[y/N]" }
	$ans = Read-Host "$Prompt $tail"
	if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
	return ($ans -match '^(y|Y)')
}

function Read-VersionSimple {
	param([string]$Default="1.0.0")
	$ans = Read-Host "App version (e.g. 1.2.3 or 1.2.3-rc.1) [$Default]"
	if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
	return $ans
}

# 1) Menu
function Get-NextVersion {
	$vf = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'version.json'
	$verNum = 0
	try { if (Test-Path $vf) { $d = Get-Content $vf | ConvertFrom-Json; $verNum = [int]$d.version } } catch {}
	$next = $verNum + 1
	$major = [math]::Floor($next/100); $minor=[math]::Floor(($next%100)/10); $patch=$next%10
	return @{ int=$next; semver="$major.$minor.$patch" }
}

$choice = Read-Host "Выберите действие: [1] Локально +1, [2] Релиз +1, [3] Пуш тега, [4] Произв. версия"

switch ($choice) {
	'1' {
		$nv = Get-NextVersion
		$cmd = ".\\build.ps1 -Version $($nv.int)"
		Write-Host "Running: $cmd" -ForegroundColor DarkGray
		& powershell -NoProfile -ExecutionPolicy Bypass -Command $cmd
		Write-Host "Done." -ForegroundColor Green
		return
	}
	'2' {
		$nv = Get-NextVersion
		$cmd = ".\\build.ps1 -Version $($nv.int) -PublishRelease -TagPrefix v"
		Write-Host "Running: $cmd" -ForegroundColor DarkGray
		& powershell -NoProfile -ExecutionPolicy Bypass -Command $cmd
		Write-Host "Done." -ForegroundColor Green
		return
	}
	'3' {
		$nv = Get-NextVersion
		$tag = Read-Host "Git tag to push [v$($nv.semver)]"; if ([string]::IsNullOrWhiteSpace($tag)) { $tag = "v$($nv.semver)" }
		Write-Host "Creating and pushing tag $tag" -ForegroundColor Cyan
		git tag -d $tag 2>$null | Out-Null; git tag $tag; git push origin $tag
		Write-Host "Done." -ForegroundColor Green
		return
	}
	default {
		$version = Read-VersionSimple
		$defaultTag = "v$version"
		$tag = Read-Host "Git tag to push [$defaultTag]"; if ([string]::IsNullOrWhiteSpace($tag)) { $tag = $defaultTag }
		$localOnly = Read-YesNo -Prompt "Local build only (no release)?" -Default:$true
	}
}

# 2) Execute
if ($localOnly) {
	$cmd = ".\\build.ps1 -Version `"$version`""
	Write-Host "Running: $cmd" -ForegroundColor DarkGray
	& powershell -NoProfile -ExecutionPolicy Bypass -Command $cmd
} else {
	Write-Host "Creating and pushing tag $tag" -ForegroundColor Cyan
	git tag -d $tag 2>$null | Out-Null
	git tag $tag
	git push origin $tag
}

Write-Host "Done." -ForegroundColor Green
