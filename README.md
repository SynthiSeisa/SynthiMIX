# SynthiMIX

AuroMIX Musik-Tool zum Verwalten, Analysieren und Herunterladen von Musik.  
Gebaut mit Electron + Svelte + Python (FastAPI).

## Features

- **Bibliothek** — Musik-Ordner scannen, LUFS/BPM analysieren, Duplikate erkennen
- **Queue & Player** — Drag & Drop, Crossfade, Automix
- **Downloads** — YouTube / Playlists per yt-dlp herunterladen
- **Wiedergabe-Verlauf** — Automix basiert auf den zuletzt gespielten Tracks
- **Notizblock** — Notizen während des Sets speichern

## Voraussetzungen

| Tool | Version |
|------|---------|
| [Node.js](https://nodejs.org/) | 20+ |
| [Python](https://www.python.org/) | 3.11+ |
| [PyInstaller](https://pyinstaller.org/) | `pip install pyinstaller` |

Außerdem müssen folgende Binaries manuell in den `bin/` Ordner gelegt werden:

- [`ffmpeg.exe`](https://ffmpeg.org/download.html)
- [`ffprobe.exe`] (im selben Paket wie ffmpeg)
- [`yt-dlp.exe`](https://github.com/yt-dlp/yt-dlp/releases/latest)

## Installation & Build

```powershell
# Repository klonen
git clone https://github.com/SynthiSeisa/SynthiMIX.git
cd SynthiMIX

# Abhängigkeiten installieren
cd app && npm install && cd ..
npm install

# Kompletten Build ausführen (Backend + Frontend + Installer)
.\build.ps1
```

Der fertige Installer liegt danach in `dist-installer\`.

## Entwicklung (ohne Build)

```powershell
.\start-dev.ps1
```

Startet Backend (Python) und Frontend (Vite + Electron) im Dev-Modus.

## Projektstruktur

```
├── app/
│   ├── electron/       # Electron Hauptprozess
│   └── src/            # Svelte Frontend
│       ├── components/ # UI-Komponenten
│       └── stores/     # Svelte Stores (WebSocket etc.)
├── backend/
│   └── main.py         # Python FastAPI Backend
├── bin/                # ffmpeg, ffprobe, yt-dlp (nicht im Repo)
├── build-resources/    # App-Icon
└── build.ps1           # Build-Skript
```
