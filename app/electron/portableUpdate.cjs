// Update im tragbaren Betrieb (Programm auf einer externen Platte): nur die
// Programmdateien auf der Platte ersetzen — ohne den Windows-Installer.
// Der Installer kennt je Benutzer nur EINEN Installationsort (Registry). Hing
// die Platte an einem PC, auf dem SynthiMIX auch fest installiert ist, landete
// das Update dort statt auf der Platte (gemeldet 10/2026).
//
// Ablauf: der schon heruntergeladene und geprueften Installer ist ein
// 7-Zip-Archiv mit den Programmdateien. Ein kleines PowerShell-Skript wartet,
// bis das Programm zu ist, entpackt ihn in einen Zwischenordner, kopiert die
// Dateien ueber den Programmordner und startet das Programm wieder. Geht etwas
// schief, bleibt die alte Fassung und startet wieder; der Grund steht im
// Protokoll (portable-update.log im Datenordner).
const path = require('path')

const q = (s) => "'" + String(s).replace(/'/g, "''") + "'"       // PowerShell-Literal

/** Text des Skripts. relaunch=false: nach dem Kopieren nichts starten (Tests). */
function script({ installer, installDir, sevenZip, tmpDir, logFile, exeName = 'SynthiMIX.exe', relaunch = true, waitSec = 60 }) {
  return `$ErrorActionPreference = 'Stop'
$installer = ${q(installer)}
$dir = ${q(installDir)}
$zip = ${q(sevenZip)}
$tmp = ${q(tmpDir)}
$log = ${q(logFile)}
$exe = Join-Path $dir ${q(exeName)}
function Note($m) { try { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $m) -Encoding UTF8 } catch {} }
$ok = $false
try {
  # warten, bis das Programm (und sein Dienst) von diesem Ordner beendet ist
  $left = $null
  for ($i = 0; $i -lt ${Math.max(1, waitSec * 2)}; $i++) {
    $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dir + '\\', [StringComparison]::OrdinalIgnoreCase) })
    if ($left.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
  }
  if ($left.Count -gt 0) { throw 'Das Programm laeuft noch.' }
  if (-not (Test-Path -LiteralPath $installer)) { throw 'Update-Datei fehlt.' }
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  # 7za aus dem Programmordner erst beiseite legen: er wird gleich ueberschrieben
  $z = Join-Path $env:TEMP ('synthimix-7za-' + $PID + '.exe')
  Copy-Item -LiteralPath $zip -Destination $z -Force
  & $z x $installer ('-o' + $tmp) -y | Out-Null
  $code = $LASTEXITCODE
  Remove-Item -LiteralPath $z -Force -ErrorAction SilentlyContinue
  if ($code -ne 0) { throw ('Entpacken fehlgeschlagen (7za ' + $code + ').') }
  if (-not (Test-Path -LiteralPath (Join-Path $tmp ${q(exeName)}))) { throw 'Im Update fehlt das Programm.' }
  # nichts loeschen, nur ersetzen und ergaenzen
  robocopy $tmp $dir /E /R:3 /W:2 /NFL /NDL /NP /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) { throw ('Kopieren fehlgeschlagen (robocopy ' + $LASTEXITCODE + ').') }
  $ok = $true
  Note ('Update eingespielt: ' + (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion)
} catch {
  Note ('FEHLER: ' + $_.Exception.Message)
} finally {
  try { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force } } catch {}
  ${relaunch ? "if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -ArgumentList $(if ($ok) { '--updated' } else { '--update-failed' }) }" : '# kein Neustart'}
}
if (-not $ok) { exit 1 }
`
}

/**
 * Update starten: Skript schreiben und losgeloest ausfuehren. Der Aufrufer
 * beendet danach das Programm. Liefert false, wenn etwas fehlt (dann nichts tun).
 */
function start({ installer, installDir, resourcesDir, dataDir, fs, spawn, tmpRoot }) {
  const sevenZip = path.join(resourcesDir, '7za.exe')
  if (!installer || !fs.existsSync(installer) || !fs.existsSync(sevenZip)) return false
  const stamp = Date.now()
  const ps1 = path.join(tmpRoot, `synthimix-update-${stamp}.ps1`)
  const text = script({
    installer, installDir, sevenZip,
    tmpDir: path.join(tmpRoot, `synthimix-update-${stamp}`),
    logFile: path.join(dataDir, 'portable-update.log'),
  })
  fs.writeFileSync(ps1, '﻿' + text, 'utf-8')        // BOM: sonst liest PowerShell 5 Umlaute im Pfad falsch
  const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ps1],
                      { detached: true, stdio: 'ignore', windowsHide: true })
  child.unref()
  return true
}

module.exports = { script, start }
