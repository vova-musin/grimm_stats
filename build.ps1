param(
	[string]$Python = "python",
	[string]$Name = "GrimmStats",
	[string]$CopyTo = "C:\My Drive\Grimm",
	[switch]$AutoRelease = $false,
	[switch]$PublishRelease = $false,   # алиас для совместимости
	[string]$CommitMessage = "Auto build",
	[string]$Version = "",             # Можно задать X.Y.Z или целое; если пусто — авто +1
	[string]$TagPrefix = "v",          # префикс для тега релиза

	# Подписывание (уменьшает блокировки SmartScreen/браузерами)
	[switch]$Sign = $false,
	[string]$PfxPath = "",             # путь к .pfx (если используется PFX)
	[SecureString]$PfxPassword = $null, # пароль PFX (SecureString; можно оставить пустым)
	[string]$CertThumbprint = "",      # отпечаток сертификата в хранилище (альтернатива PFX)
	[string]$TimestampUrl = "http://timestamp.digicert.com", # RFC3161 TSA
	[string]$SigntoolPath = ""         # явный путь к signtool.exe (если не в PATH)
)

$ErrorActionPreference = "Stop"

<#
	Автоподхват параметров подписи из переменных окружения:
	- SIGN_PFX_PATH
	- SIGN_PFX_PASSWORD
	- SIGN_CERT_THUMBPRINT
	- SIGN_TOOL
	- SIGN_TIMESTAMP_URL
#>
if (-not $PfxPath -and $env:SIGN_PFX_PATH) { $PfxPath = $env:SIGN_PFX_PATH }
if (-not $PfxPassword -and $env:SIGN_PFX_PASSWORD) { $PfxPassword = (ConvertTo-SecureString $env:SIGN_PFX_PASSWORD -AsPlainText -Force) }
if (-not $CertThumbprint -and $env:SIGN_CERT_THUMBPRINT) { $CertThumbprint = $env:SIGN_CERT_THUMBPRINT }
if (-not $SigntoolPath -and $env:SIGN_TOOL) { $SigntoolPath = $env:SIGN_TOOL }
if (-not $TimestampUrl -and $env:SIGN_TIMESTAMP_URL) { $TimestampUrl = $env:SIGN_TIMESTAMP_URL }
if (-not $Sign -and ($PfxPath -or $CertThumbprint)) { $Sign = $true }

function Resolve-SignTool {
	param(
		[string]$ExplicitPath
	)
	if ($ExplicitPath -and (Test-Path $ExplicitPath)) { return $ExplicitPath }

	$cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
	if ($cmd) { return $cmd.Source }

	$possibleRoots = @(
		Join-Path $env:ProgramFiles "Windows Kits\10\bin",
		Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
	) | Where-Object { $_ -and (Test-Path $_) }

	foreach ($root in $possibleRoots) {
		# Пытаемся найти x64/signtool.exe в подкаталогах (берём самый новый)
		$found = Get-ChildItem -Path $root -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
			Where-Object { $_.FullName -match "\\x64\\signtool.exe$" } |
			Sort-Object FullName -Descending |
			Select-Object -First 1
		if ($found) { return $found.FullName }
	}

	throw "signtool.exe не найден. Установите Windows 10 SDK или укажите -SigntoolPath."
}

function Set-FileSignature {
	param(
		[string]$FilePath,
		[string]$Signtool,
		[string]$Timestamp,
		[string]$Pfx,
		[SecureString]$PfxPwd,
		[string]$Sha1
	)
	if (-not (Test-Path $FilePath)) { return }
	Write-Host "[sign] $FilePath" -ForegroundColor Yellow

	# Предпочтительно RFC3161 (-tr/-td). Резервно можно -t (AuthentiCode), но оставим только RFC3161.
	if ($Pfx) {
		$signArgs = @('sign','/fd','sha256','/f',$Pfx)
		if ($PfxPwd) {
			$ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($PfxPwd)
			try {
				$plain = [System.Runtime.InteropServices.Marshal]::PtrToStringUni($ptr)
				if ($plain) { $signArgs += @('/p',$plain) }
			} finally {
				[System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($ptr)
			}
		}
		$signArgs += @('/tr',$Timestamp,'/td','sha256',$FilePath)
		& $Signtool @signArgs | Out-Null
	} elseif ($Sha1) {
		$signArgs = @('sign','/fd','sha256','/sha1',$Sha1,'/tr',$Timestamp,'/td','sha256',$FilePath)
		& $Signtool @signArgs | Out-Null
	} else {
		throw 'No code-signing certificate provided. Use -PfxPath or -CertThumbprint.'
	}

	if ($LASTEXITCODE -ne 0) { throw "Signing failed: $FilePath" }
}

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

Write-Host "[pre] Syntax check (main.py, updater.py)" -ForegroundColor Cyan
& $venvPython -m py_compile main.py
if ($LASTEXITCODE -ne 0) { Write-Error "Python syntax check failed: main.py"; exit 1 }
& $venvPython -m py_compile updater.py
if ($LASTEXITCODE -ne 0) { Write-Error "Python syntax check failed: updater.py"; exit 1 }

Write-Host "[4/6] Prepare icon (PNG -> ICO if needed)" -ForegroundColor Cyan
$pngCandidates = @("icon.png", "photo_2025-09-21_18-08-53.png")
$pngPath = $null
foreach ($p in $pngCandidates) { if (Test-Path $p) { $pngPath = $p; break } }
if (-not (Test-Path "icon.ico") -and $pngPath) {
    try {
        Write-Host "Converting $pngPath -> icon.ico" -ForegroundColor Cyan
        $pyLines = @(
            'from PIL import Image',
            'import sys',
            'src = sys.argv[1]',
            'im = Image.open(src).convert("RGBA")',
            'sizes = [(256,256),(128,128),(64,64),(32,32),(16,16)]',
            'im.save("icon.ico", sizes=sizes)'
        )
        $py = [string]::Join("`n", $pyLines)
        & $venvPython -c $py $pngPath
    } catch {
        Write-Warning "Не удалось сконвертировать PNG в ICO. Продолжаю без иконки окна."
    }
}

# Discord icon (download and convert to PNG if missing)
if (-not (Test-Path "discord.png")) {
    try {
        Write-Host "Downloading discord icon" -ForegroundColor Cyan
        Invoke-WebRequest -Uri "https://cojo.ru/wp-content/uploads/2022/12/znachok-discord-1.webp" -OutFile "discord.webp" -UseBasicParsing
        $pyDl = @'
from PIL import Image
img = Image.open("discord.webp").convert("RGBA")
# небольшая иконка 16x16, чтобы влезала в кнопку
img = img.resize((16,16))
img.save("discord.png")
'@
        & $venvPython -c $pyDl
    } catch {
        Write-Warning "Не удалось получить иконку Discord"
    }
}

Write-Host "[5/7] Update version in manifest" -ForegroundColor Cyan
# Читаем текущую версию и инкрементируем
$versionFile = "version.json"
$buildVersionInt = 1
$buildSemver = ""
$buildDate = Get-Date -Format "yyyy-MM-dd"

# 1) Если передан параметр -Version, используем его
if ($Version) {
    if ($Version -is [string] -and $Version.Contains('.')) {
        # Формат X.Y.Z -> конвертируем в integer
        $parts = $Version.Split('.')
        if ($parts.Length -ge 3) {
            try {
                $maj = [int]$parts[0]; $min = [int]$parts[1]; $pat = [int]$parts[2]
                $buildVersionInt = ($maj*10000) + ($min*100) + $pat
                $buildSemver = "$maj.$min.$pat"
                Write-Host "Using explicit version: $buildSemver ($buildVersionInt)" -ForegroundColor Yellow
            } catch {
                Write-Warning "Invalid -Version format. Expected X.Y.Z. Ignoring parameter."
                $Version = ""
            }
        } else {
            Write-Warning "Invalid -Version format. Expected X.Y.Z. Ignoring parameter."
            $Version = ""
        }
    } else {
        # Формат integer -> конвертируем в X.Y.Z
        try {
            $buildVersionInt = [int]$Version
        } catch {
            Write-Warning "Invalid -Version format. Expected integer or X.Y.Z. Ignoring parameter."
            $Version = ""
        }
    }
}

# 2) Если версия не задана вручную через параметр -Version, инкрементируем предыдущую
if (-not $Version) {
    if (Test-Path $versionFile) {
        try {
            $versionData = Get-Content $versionFile | ConvertFrom-Json
            $buildVersionInt = [int]$versionData.version + 1
            Write-Host "Incrementing version: $($versionData.version) -> $buildVersionInt" -ForegroundColor Yellow
        } catch {
            Write-Warning "Failed to read version, using version 1"
        }
    }
}

# 3) Конвертируем integer версию в semver (X.Y.Z), если не задан напрямую
if (-not $buildSemver) {
    $major = [math]::Floor($buildVersionInt / 10000)
    $minor = [math]::Floor(($buildVersionInt % 10000) / 100)
    $patch = $buildVersionInt % 100
    $buildSemver = "$major.$minor.$patch"
}

# Обновляем манифест
$manifest = @{
    version = $buildVersionInt
    semver = $buildSemver
	build_date = $buildDate
    exe_url = "https://github.com/vova-musin/grimm_stats/releases/download/v$buildSemver/GrimmStats.exe"
    exe_file_id = ""
    manifest_file_id = ""
    changelog = @("Version $buildVersionInt ($buildSemver) - auto build from $buildDate")
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content $versionFile -Encoding UTF8

Write-Host "[6/7] Clean previous builds" -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build,dist
Remove-Item -Force -ErrorAction SilentlyContinue *.spec

# Формируем аргументы PyInstaller корректно
$piArgs = @('--noconfirm','--clean','--onefile','--windowed','--name', $Name)
if (Test-Path "icon.ico") {
	$piArgs += @('--icon','icon.ico')
	$piArgs += @('--add-data','icon.ico;.')
} else {
	Write-Host "(опционально) Положите icon.ico или icon.png в корень проекта, чтобы задать иконку." -ForegroundColor Yellow
}
if (Test-Path $versionFile) {
    # Вкладываем version.json внутрь onefile, чтобы приложение могло читать локальную версию из _MEIPASS
	$piArgs += @('--add-data',"$versionFile;.")
}
if (Test-Path "discord.png") { $piArgs += @('--add-data','discord.png;.') }

Write-Host "[7/8] Build updater (onefile, console)" -ForegroundColor Cyan
& $venvPython -m PyInstaller --noconfirm --clean --onefile --name updater updater.py | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path 'dist/updater.exe')) { Write-Error "Build failed: updater.exe"; exit 1 }

# Встраиваем updater.exe внутрь основного onefile
if (Test-Path "dist/updater.exe") {
	$piArgs += @('--add-binary','dist/updater.exe;.')
}

# Подписываем updater.exe до встраивания (если включено)
if ($Sign -and (Test-Path 'dist/updater.exe')) {
	try {
		$tool = Resolve-SignTool -ExplicitPath $SigntoolPath
		Set-FileSignature -FilePath 'dist/updater.exe' -Signtool $tool -Timestamp $TimestampUrl -Pfx $PfxPath -PfxPwd $PfxPassword -Sha1 $CertThumbprint
		Write-Host "[post] updater.exe signed" -ForegroundColor Green
	} catch {
		Write-Warning $_
	}
}

Write-Host "[8/8] Build main with PyInstaller" -ForegroundColor Cyan
& $venvPython -m PyInstaller @piArgs main.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path 'dist' ("{0}.exe" -f $Name)))) { Write-Error "Build failed: $Name.exe"; exit 1 }

# Опционально: подписываем бинарники для уменьшения блокировок SmartScreen/браузерами
if ($Sign) {
	try {
		$tool = Resolve-SignTool -ExplicitPath $SigntoolPath
		$mainExe = Join-Path "dist" ("{0}.exe" -f $Name)
		$updExe = Join-Path "dist" "updater.exe"
		Set-FileSignature -FilePath $mainExe -Signtool $tool -Timestamp $TimestampUrl -Pfx $PfxPath -PfxPwd $PfxPassword -Sha1 $CertThumbprint
		Set-FileSignature -FilePath $updExe -Signtool $tool -Timestamp $TimestampUrl -Pfx $PfxPath -PfxPwd $PfxPassword -Sha1 $CertThumbprint
		Write-Host "[post] Code signing done" -ForegroundColor Green
	} catch {
		Write-Warning $_
	}
}

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
if ($AutoRelease -or $PublishRelease) {
	Write-Host "`n[GitHub] Auto-release to GitHub" -ForegroundColor Cyan
	
	# Проверяем, что git настроен
	git status 2>&1 | Out-Null
	if ($LASTEXITCODE -ne 0) {
		Write-Warning "Git not initialized or error. Skipping auto-release."
		exit 0
	}

	# Синхронизация с origin/main (fetch + rebase), чтобы пушы проходили стабильно
	Write-Host "[GitHub] Sync main with origin (fetch + rebase)" -ForegroundColor Yellow
	$__prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
	try {
		git fetch origin main 2>&1 | Out-Null
		git rebase --autostash origin/main 2>&1 | Out-Null
	} catch {
		Write-Host "Rebase failed, trying pull --rebase" -ForegroundColor DarkYellow
		try { git pull --rebase origin main 2>&1 | Out-Null } catch { Write-Warning $_ }
	} finally { $ErrorActionPreference = $__prevEAP; $LASTEXITCODE = 0 }
	
	Write-Host "[GitHub] Adding files (git add -A)..." -ForegroundColor Yellow
	git add -A
	
	# Коммитим только если есть изменения
	$pending = git status --porcelain
	if ($pending) {
		Write-Host "[GitHub] Committing changes..." -ForegroundColor Yellow
git commit -m "$CommitMessage - v$buildSemver" 2>&1 | Out-Null
		if ($LASTEXITCODE -ne 0) {
			Write-Host "No changes to commit or commit failed" -ForegroundColor Gray
		}
	} else {
		Write-Host "[GitHub] No changes to commit" -ForegroundColor Gray
	}
	
$tagName = "$TagPrefix$buildSemver"
	Write-Host "[GitHub] Creating tag $tagName..." -ForegroundColor Yellow
	# Удаляем тег если существует (для перезаписи) — без ошибок, если его нет
	$existingTag = (git tag --list "$tagName" 2>$null)
	if ($existingTag) { git tag -d "$tagName" | Out-Null }
	$LASTEXITCODE = 0  # Сбрасываем код ошибки
	git tag "$tagName"
	
    Write-Host "[GitHub] Pushing to GitHub (main, best-effort)..." -ForegroundColor Yellow
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $pushOutput = git push origin main 2>&1
        if ($LASTEXITCODE -ne 0 -and $pushOutput -notmatch "Everything up-to-date") {
            Write-Warning "Failed to push main branch: $pushOutput"
        }
    } catch {
        Write-Warning "Push main failed: $_"
    } finally {
        $ErrorActionPreference = $prevEAP
        $LASTEXITCODE = 0
    }
	
Write-Host "[GitHub] Pushing tag $tagName..." -ForegroundColor Yellow
$__prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { $tagOutput = git push origin "$tagName" --force 2>&1 } catch { $tagOutput = "" }
$ErrorActionPreference = $__prevEAP

if ($LASTEXITCODE -eq 0 -or $tagOutput -match "new tag" -or $tagOutput -match "Everything up-to-date") {
		Write-Host "`nSuccessfully pushed $tagName to GitHub!" -ForegroundColor Green
		Write-Host "GitHub Actions will build and create release at:" -ForegroundColor Cyan
Write-Host "  https://github.com/vova-musin/grimm_stats/releases/tag/$tagName" -ForegroundColor White
		Write-Host "`nCheck workflow status at:" -ForegroundColor Cyan
		Write-Host "  https://github.com/vova-musin/grimm_stats/actions" -ForegroundColor White
	} else {
		Write-Warning "Failed to push tag to GitHub: $tagOutput"
	}
}
