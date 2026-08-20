# SynthiMIX — Build-Skript
# Erzeugt einen Windows-Installer (.exe) der kein Python oder Node.js benötigt.
# Voraussetzungen: Python 3.11+, Node.js 20+, pip install pyinstaller

param(
    [switch]$SkipBackend,   # -SkipBackend → PyInstaller-Schritt überspringen
    [switch]$SkipFrontend   # -SkipFrontend → Vite-Build überspringen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "    FEHLER: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Python-Backend mit PyInstaller bündeln ────────────────────────────────
if (-not $SkipBackend) {
    Step "Backend mit PyInstaller bündeln..."
    $specFile = "$root\backend\backend.spec"
    if (-not (Test-Path $specFile)) { Fail "backend.spec nicht gefunden" }

    $pyCheck = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "Python nicht gefunden. Bitte Python 3.11+ installieren." }
    Ok $pyCheck

    $piCheck = pyinstaller --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Step "PyInstaller nicht gefunden, wird installiert..."
        pip install pyinstaller
    }

    Remove-Item "$root\backend\dist" -Recurse -Force -ErrorAction SilentlyContinue
    Push-Location "$root\backend"
    pip install -r requirements.txt --quiet
    pyinstaller backend.spec --distpath dist --workpath build --noconfirm
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "PyInstaller fehlgeschlagen" }
    Pop-Location
    Ok "Backend gebündelt → backend\dist\backend\"
}

# ── 2. Svelte-Frontend bauen ─────────────────────────────────────────────────
if (-not $SkipFrontend) {
    Step "Svelte-Frontend bauen..."
    Push-Location "$root\app"
    if (-not (Test-Path "node_modules")) {
        npm install
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install fehlgeschlagen" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Vite-Build fehlgeschlagen" }
    Pop-Location
    Ok "Frontend gebaut → app\dist\"
}

# ── 3. electron-builder — Windows Installer ──────────────────────────────────
Step "Electron-Installer bauen..."
Push-Location $root

if (-not (Test-Path "node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install fehlgeschlagen" }
}

# Platzhalter-Icon falls keins vorhanden
$iconDir = "$root\build-resources"
if (-not (Test-Path "$iconDir\icon.ico")) {
    New-Item -ItemType Directory -Force $iconDir | Out-Null
    # Kopiere ein Standard-Icon oder überspringe (electron-builder nutzt dann Standard)
    Write-Host "    Hinweis: Kein Icon gefunden ($iconDir\icon.ico). Standard-Icon wird verwendet." -ForegroundColor Yellow
    # Entferne Icon-Referenz temporär aus package.json wenn nicht vorhanden
}

npx electron-builder --win --x64
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "electron-builder fehlgeschlagen" }
Pop-Location

# ── 4. Installer + portable Build in die Projektordner veröffentlichen ──────
$projectRoot   = Split-Path $root -Parent
$installerDest = "$projectRoot\1_Installer"
$portableDest  = "$projectRoot\2_Portable-App"

$version   = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version
$setupName = "SynthiMIX.Setup.$version.exe"
$setupPath = "$root\dist-installer\$setupName"

$installer = Get-ChildItem $setupPath -ErrorAction SilentlyContinue
if ($installer -and (Test-Path $installerDest)) {
    # Nur den Installer dieser Version kopieren, nicht die ganze Historie
    Copy-Item $setupPath $installerDest -Force
    Copy-Item "$setupPath.blockmap" $installerDest -Force -ErrorAction SilentlyContinue
    Ok "Installer veröffentlicht → $installerDest\$setupName"
}
if ((Test-Path "$root\dist-installer\win-unpacked") -and (Test-Path $portableDest)) {
    Remove-Item "$portableDest\*" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "$root\dist-installer\win-unpacked\*" $portableDest -Recurse -Force
    Ok "Portable App aktualisiert → $portableDest"
}

# ── 5. Release-Artefakte prüfen ─────────────────────────────────────────────
# latest.yml ist die Datei, aus der electron-updater die neueste Version liest.
# Fehlt sie im GitHub-Release, bekommt KEIN Nutzer ein Update angeboten —
# genau das ist bei v1.3.8 passiert. Darum hier hart prüfen.
Step "Release-Artefakte prüfen..."
$latestYml = "$root\dist-installer\latest.yml"
$releaseOk = $true

foreach ($f in @($setupPath, "$setupPath.blockmap", $latestYml)) {
    if (Test-Path $f) {
        Ok (Split-Path $f -Leaf)
    } else {
        Write-Host "    FEHLT: $(Split-Path $f -Leaf)" -ForegroundColor Red
        $releaseOk = $false
    }
}

if (Test-Path $latestYml) {
    $ymlVersion = (Select-String -Path $latestYml -Pattern '^version:\s*(.+)$').Matches.Groups[1].Value.Trim()
    if ($ymlVersion -ne $version) {
        Write-Host "    FEHLER: latest.yml sagt $ymlVersion, package.json sagt $version" -ForegroundColor Red
        $releaseOk = $false
    } else {
        Ok "latest.yml passt zu Version $version"
    }
}

Step "Fertig!"
if ($installer) {
    Write-Host "`n  Dieser Installer kann auf jedem Windows-PC ohne Python oder Node.js installiert werden.`n" -ForegroundColor White
} else {
    Write-Host "    Installer wurde erstellt in: $root\dist-installer\" -ForegroundColor Green
}

if ($releaseOk) {
    Write-Host "  Release veröffentlichen — alle drei Dateien werden gebraucht:" -ForegroundColor White
    Write-Host "    gh release create v$version ``"                                  -ForegroundColor Cyan
    Write-Host "      `"$setupPath`" ``"                                             -ForegroundColor Cyan
    Write-Host "      `"$setupPath.blockmap`" ``"                                    -ForegroundColor Cyan
    Write-Host "      `"$latestYml`" ``"                                             -ForegroundColor Cyan
    Write-Host "      --title `"SynthiMIX v$version`" --notes-file RELEASE-NOTES.md`n" -ForegroundColor Cyan
} else {
    Write-Host "`n  Release NICHT veröffentlichen — es fehlen Artefakte (siehe oben).`n" -ForegroundColor Red
}
