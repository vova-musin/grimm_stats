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

# 1) Ask parameters
$version = Read-VersionSimple
$defaultTag = "v$version"
$tag = Read-Host "Git tag to push [$defaultTag]"
if ([string]::IsNullOrWhiteSpace($tag)) { $tag = $defaultTag }
$localOnly = Read-YesNo -Prompt "Local build only (no release)?" -Default:$true

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
