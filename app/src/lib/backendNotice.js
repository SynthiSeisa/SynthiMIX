// Welche Meldung zeigt das Fenster, wenn der Dienst (Backend) nicht laeuft
// oder die Festplatte mit den Daten weg ist? Reine Entscheidung, ohne Fenster —
// status kommt aus Electron (backend-status), null im Browser.
// Liefert null (alles gut) oder { level: 'info'|'error', title, text, retry }.

const WAIT_INFO = 6      // s ohne Verbindung, bis ueberhaupt etwas erscheint
const WAIT_ERROR = 20    // s, bis aus "verbinde…" ein Fehler wird

export function noticeFor({ connected, downFor = 0, status = null }) {
  // Platte mit den Daten weg — auch wenn die Verbindung noch steht
  if (status?.dataGone) {
    return { level: 'error', retry: !connected,
             title: status.portable ? 'Die Festplatte mit den SynthiMIX-Daten ist nicht mehr erreichbar' : 'Der Datenordner ist nicht mehr erreichbar',
             text: `${status.dataDir} fehlt. ${status.portable ? 'Platte wieder anstecken' : 'Ordner wieder verfügbar machen'} — bis dahin wird nichts gespeichert. Danach „Erneut versuchen“ oder SynthiMIX neu starten.` }
  }
  if (connected || downFor < WAIT_INFO) return null
  if (status?.portBusy) {
    return { level: 'error', retry: true, title: 'Der SynthiMIX-Dienst kann nicht starten',
             text: 'Port 8765 ist von einem anderen Programm belegt. Läuft SynthiMIX schon ein zweites Mal (auch im Infobereich oder unter einem anderen Benutzer)? Das andere Programm beenden, dann „Erneut versuchen“.' }
  }
  if (status?.gaveUp) {
    return { level: 'error', retry: true, title: 'Der SynthiMIX-Dienst startet nicht',
             text: 'Bibliothek, Warteschlange und Downloads brauchen ihn. Unter „Details“ steht, woran es hängt — häufig hält ein Virenscanner die Datei backend.exe auf.' }
  }
  if (downFor >= WAIT_ERROR) {
    return { level: 'error', retry: !!status, title: 'Keine Verbindung zum SynthiMIX-Dienst',
             text: status?.running === false ? 'Der Dienst läuft nicht.' : 'Der Dienst antwortet nicht.' }
  }
  return { level: 'info', retry: false, title: 'Verbinde mit dem SynthiMIX-Dienst …', text: 'Einen Moment — beim ersten Start an einem PC kann das etwas dauern.' }
}
