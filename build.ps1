param(
	[string]$Python = "python",
	[string]$Name = "GrimmStats",
	[string]$CopyTo = "C:\My Drive\Grimm",
	[switch]$PublishRelease = $false,
	[string]$TagPrefix = "",
	[string]$Version = ""
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

Write-Host "[4/6] Prepare icon (PNG to ICO if needed)" -ForegroundColor Cyan
$pngCandidates = @("icon.png", "photo_2025-09-21_18-08-53.png")
$pngPath = $null
foreach ($p in $pngCandidates) { if (Test-Path $p) { $pngPath = $p; break } }
if (-not (Test-Path "icon.ico") -and $pngPath) {
	try {
		Write-Host "Converting $pngPath to icon.ico" -ForegroundColor Cyan
		$pyCode = 'from PIL import Image,sys; src=sys.argv[1]; im=Image.open(src).convert("RGBA"); sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)]; im.save("icon.ico", sizes=sizes)'
		& $venvPython -c $pyCode $pngPath
	} catch {
		Write-Warning "Failed to convert PNG to ICO. Continuing without window icon."
	}
}

Write-Host "[5/7] Update version in manifest" -ForegroundColor Cyan
$versionFile = "version.json"
$version = $null
$semver = ""
$buildDate = Get-Date -Format "yyyy-MM-dd"

if ($Version) {
    if ($Version.Contains('.')) {
        $parts = $Version.Split('.')
        if ($parts.Length -ge 3) {
            try {
                $maj = [int]$parts[0]; $min = [int]$parts[1]; $pat = [int]$parts[2]
                $version = $maj*100 + $min*10 + $pat
                $semver = "$maj.$min.$pat"
                Write-Host "Using explicit version: $semver ($version)" -ForegroundColor Yellow
            } catch {
                Write-Warning "Invalid -Version format. Expected X.Y.Z. Ignoring parameter."
                $Version = ""
            }
        } else {
            Write-Warning "Invalid -Version format. Expected X.Y.Z. Ignoring parameter."
            $Version = ""
        }
    } else {
        try {
            $version = [int]$Version
        } catch {
            Write-Warning "Invalid -Version format. Expected integer or X.Y.Z. Ignoring parameter."
            $Version = ""
        }
    }
}

if (-not $version) {
    if (Test-Path $versionFile) {
        try {
            $versionData = Get-Content $versionFile | ConvertFrom-Json
            $version = [int]$versionData.version + 1
            Write-Host "Incrementing version: $($versionData.version) -> $version" -ForegroundColor Yellow
        } catch {
            $version = 1
            Write-Warning "Failed to read version, using version 1"
        }
    } else {
        $version = 1
    }
}

if (-not $semver) {
    $semver = "{0}.{1}.{2}" -f ([math]::Floor($version/100)), ([math]::Floor(($version % 100)/10)), ($version % 10)
}

$manifest = @{
    version = $version
    build_date = $buildDate
    semver = $semver
    exe_url = "https://github.com/vova-musin/grimm_stats/releases/download/$($TagPrefix + $semver)/GrimmStats.exe"
    exe_file_id = ""
    manifest_file_id = ""
    changelog = @("Version $version ($semver) - auto build from $buildDate")
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content $versionFile -Encoding UTF8

Write-Host "[6/7] Clean previous builds" -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build,dist
Remove-Item -Force -ErrorAction SilentlyContinue *.spec

$args = @('--noconfirm','--clean','--onefile','--windowed','--name', $Name)
if (Test-Path "icon.ico") {
	$args += @('--icon','icon.ico')
	$args += @('--add-data','icon.ico;.')
} else {
	Write-Host "(optional) Place icon.ico or icon.png in project root to set icon." -ForegroundColor Yellow
}
if (Test-Path $versionFile) {
    $args += @('--add-data',"$versionFile;.")
}

Write-Host "[7/7] Build with PyInstaller" -ForegroundColor Cyan
& $venvPython -m PyInstaller @args main.py

Write-Host "[post] Build updater" -ForegroundColor Cyan
& $venvPython -m PyInstaller --noconfirm --clean --onefile --name updater updater.py | Out-Null

if ($CopyTo) {
	if (-not (Test-Path $CopyTo)) {
		Write-Host "[post] Creating destination folder: $CopyTo" -ForegroundColor Cyan
		New-Item -ItemType Directory -Force -Path $CopyTo | Out-Null
	}
	$dstExe = Join-Path $CopyTo ("{0}.exe" -f $Name)
	if (Test-Path "dist/$Name.exe") {
		Write-Host "[post] Copying dist/$Name.exe to $dstExe" -ForegroundColor Cyan
		Copy-Item -Force "dist/$Name.exe" $dstExe
	}
	if (Test-Path "dist/updater.exe") {
		$dstUpd = Join-Path $CopyTo "updater.exe"
		Write-Host "[post] Copying dist/updater.exe to $dstUpd" -ForegroundColor Cyan
		Copy-Item -Force "dist/updater.exe" $dstUpd
	}
	if (Test-Path $versionFile) {
		$dstVer = Join-Path $CopyTo "version.json"
		Write-Host "[post] Copying $versionFile to $dstVer" -ForegroundColor Cyan
		Copy-Item -Force $versionFile $dstVer
	}
}

Write-Host "Done. EXE: dist/$Name.exe" -ForegroundColor Green

if ($PublishRelease) {
    try {
        $owner = "vova-musin"; $repo = "grimm_stats"
        if (Get-Command gh -ErrorAction SilentlyContinue) {
            $tag = "$TagPrefix$semver"
            Write-Host "[release] $tag (gh)" -ForegroundColor Cyan
            gh release create $tag --title "$tag" --notes "Auto build $tag" 2>$null | Out-Null
            if (Test-Path "dist/$Name.exe") { gh release upload $tag "dist/$Name.exe" --clobber | Out-Null }
        } elseif ($env:GITHUB_TOKEN) {
            Write-Host "[release] using GitHub API" -ForegroundColor Cyan
            $token = $env:GITHUB_TOKEN
            $base = "https://api.github.com"; $headers = @{ Authorization = "token $token"; "User-Agent" = "grimm-stats-build" }
            $tag = "$TagPrefix$semver"
            try {
                $body = @{ tag_name = $tag; name = $tag; body = "Auto build $tag"; draft = $false; prerelease = $false } | ConvertTo-Json
                $rel = Invoke-RestMethod -Method Post -Uri "$base/repos/$owner/$repo/releases" -Headers $headers -Body $body -ContentType "application/json"
            } catch { $rel = Invoke-RestMethod -Method Get -Uri "$base/repos/$owner/$repo/releases/tags/$tag" -Headers $headers }
            if (-not $rel) { throw "release create failed" }
            $asset = Join-Path $PSScriptRoot "dist/$Name.exe"
            $upload = "https://uploads.github.com/repos/$owner/$repo/releases/$($rel.id)/assets?name=$Name.exe"
            Invoke-RestMethod -Method Post -Uri $upload -Headers @{ Authorization = "token $token"; "Content-Type" = "application/octet-stream"; "User-Agent" = "grimm-stats-build" } -InFile $asset -ContentType "application/octet-stream" | Out-Null
        } else {
            Write-Warning "No gh and no GITHUB_TOKEN - skip release publish."
        }
    } catch {
        $msg = ('Release publish failed: {0}' -f ($_.Exception.Message))
        Write-Warning $msg
    }
}