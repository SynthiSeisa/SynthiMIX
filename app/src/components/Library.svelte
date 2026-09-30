<script>
  import { onMount, untrack, tick } from 'svelte'
  import { library, scanStatus, playlists, send, normalizeProgress, dlHistory, scanRecursive, playlistContent, appSettings, downloadTree, downloadTreeLoaded, skipNextCrossfade, analyzeProgress, selectionOwner, favorites, trackIdentified, acoustidApiKey, openSettings, connected, qualityScan, revealPath, excludedFolders, watchedFolders, downloadDir, startQualityBatch } from '../stores/ws.js'
  import BetterVersionDialog from './BetterVersionDialog.svelte'
  import { keySortValue } from '../lib/keys.js'
  import KeyChip from './KeyChip.svelte'
  import { density } from '../lib/prefs.js'
  import DuplicateScanDialog from './DuplicateScanDialog.svelte'
  import GenreDialog from './GenreDialog.svelte'
  import TitleDialog from './TitleDialog.svelte'

  let showDupeScan     = $state(false)
  let showGenres       = $state(false)
  let titlePaths       = $state(undefined) // Titel aufraeumen: null = alle, Liste = Auswahl
  let fpPaths          = $state(null)      // Fingerprint-Erkennung fuer mehrere Titel
  let showPlDupeScan   = $state(false)
  let editTrack        = $state(null)   // { path, title, artist } — metadata edit dialog
  let plNameDialog     = $state(false)  // add playlist dialog
  let plNameValue      = $state('')
  let plDeletePath     = $state(null)   // path to confirm delete

  function focus(el) { setTimeout(() => el?.focus(), 50) }

  // ── Nav state ─────────────────────────────────────────────────────────────
  let navMode   = $state('all')
  let search    = $state('')
  let sortCol   = $state('title')
  let sortAsc   = $state(true)
  let selected  = $state(new Set())

  // ── Derive unique folders from library ────────────────────────────────────
  const folders = $derived(
    [...new Set($library.map(t => t.folder).filter(Boolean))].sort()
  )

  // ── Strip leading track numbers from filenames ────────────────────────────
  // "01. Artist - Title" → "Artist - Title", "1 - Artist - Title" → "Artist - Title"
  function stripTrackNumber(s) {
    return s.replace(/^\d{1,3}[\.\s\-]+(?=[^\d])/u, '').trim()
  }

  // ── Clean a raw artist string: strip features, x-collabs, comma-lists ──────
  function _cleanArtist(raw) {
    return raw
      .replace(/\s*[\(\[](feat|ft|featuring|with|prod)\.?[^\)\]]*[\)\]]/gi, '')
      .replace(/\s+(feat|ft|featuring)\b.+$/gi, '')
      .replace(/\s+x\s+.+$/i, '')        // "A x B" → "A"  (" x " always = collab)
      .replace(/\s+vs\.?\s+.+$/i, '')    // "A vs B" → "A"
      .split(/,\s+| and (?=[A-Z])/)[0]   // "A, B" or "A and B" → "A"
      .trim()
  }

  // ── Primary artist — used for filtering, needs standalone-set context ──────
  function primaryArtist(raw, standaloneSet) {
    if (!raw) return ''
    const s = _cleanArtist(raw)
    return _resolvePrimary(s, standaloneSet ?? _standaloneArtists)
  }

  // Resolve "A & B" → primary using known standalone artists
  function _resolvePrimary(s, standaloneSet) {
    const parts = s.split(' & ')
    if (parts.length === 1) return s
    if (parts.length > 2) return parts[0] + ' & ' + parts[1]  // "A & B & C" → "A & B"
    // "A & B": if A is a known standalone artist → collaboration → use A
    return standaloneSet?.has(parts[0].toLowerCase()) ? parts[0] : s
  }

  // ── Derive artists with two-pass algorithm ─────────────────────────────────
  // Pass 1: find all artists that appear WITHOUT "&" (= solo / band names)
  // Pass 2: for "A & B", check if A is in the standalone set → group under A
  let _standaloneArtists = new Set()
  const artists = $derived.by(() => {
    const raws = []
    for (const t of $library) {
      const raw = getTrackArtistTitle(t).artist.trim()
      if (raw) raws.push(_cleanArtist(raw))
    }

    // Pass 1: standalone = cleaned artists with no "&"
    const standaloneSet = new Set(raws.filter(r => !r.includes(' & ')).map(r => r.toLowerCase()))
    _standaloneArtists = standaloneSet

    // Pass 2: determine primary for each
    const seen = new Map()
    for (const r of raws) {
      if (!r) continue
      const primary = _resolvePrimary(r, standaloneSet)
      const key = primary.toLowerCase()
      if (!seen.has(key)) seen.set(key, primary)
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b, 'de'))
  })

  const albums = $derived.by(() => {
    const seen = new Map()
    for (const t of $library) {
      const a = (t.album ?? '').trim()
      if (a) { const k = a.toLowerCase(); if (!seen.has(k)) seen.set(k, a) }
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b, 'de'))
  })

  const genres = $derived.by(() => {
    const seen = new Map()
    for (const t of $library) {
      for (const part of (t.genre ?? '').split(/[,;/]/)) {
        const g = part.trim()
        if (g) { const k = g.toLowerCase(); if (!seen.has(k)) seen.set(k, g) }
      }
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b, 'de'))
  })

  // ── Download directory tree ───────────────────────────────────────────────
  let dlFolderOpen  = $state({})  // folder path → bool

  function loadDlTree() {
    send({ type: 'get_download_tree' })
  }

  function selectNav(mode) {
    // Der Nav-Eintrag zeigt immer die ganze Bibliothek; Ordner und Playlisten
    // prueft man gezielt ueber deren Kontextmenue ("Auf Duplikate scannen").
    if (mode === 'duplicates') dupeSourceMode = 'all'
    if (mode === 'downloads' || mode.startsWith('playlist:')) dlColSort = false
    navMode = mode
    search  = ''
    selected = new Set()
  }

  // ── Nav section expand/collapse ───────────────────────────────────────────
  // Auf-/zugeklappte Abschnitte und die zuletzt offene Ansicht werden gemerkt.
  // Vorher stand die Nav nach jedem Start wieder im Auslieferungszustand,
  // waehrend angeheftete Ordner und Spaltenbreiten laengst gespeichert wurden.
  const NAV_KEY   = 'synthimix-nav'
  const _navSaved = (() => { try { return JSON.parse(localStorage.getItem(NAV_KEY) ?? '{}') } catch { return {} } })()
  const _secSaved = _navSaved.sections ?? {}
  let secFsOpen       = $state(_secSaved.fs       ?? false)
  let secArtistOpen   = $state(_secSaved.artist   ?? false)
  let secAlbumOpen    = $state(_secSaved.album    ?? false)
  let secGenreOpen    = $state(_secSaved.genre    ?? false)
  let secDlOpen       = $state(_secSaved.dl       ?? true)
  let secPlaylistOpen = $state(_secSaved.playlist ?? true)
  let secQuickOpen    = $state(_secSaved.quick    ?? true)
  let secDirsOpen     = $state(_secSaved.dirs     ?? true)
  let secFilterOpen   = $state(_secSaved.filter   ?? false)

  // Nur Ansichten, die sich allein aus Bibliothek, Playlisten oder dem
  // Download-Baum ergeben. "Mein Computer" braeuchte eine Ordnerabfrage,
  // "Duplikate" einen Scan — die startet man lieber bewusst.
  function restorableMode(mode) {
    if (mode === 'history' || mode === 'dl_recent') return 'downloads'
    if (mode === 'dl_all') return 'downloads'
    if (['all', 'recent', 'downloads', 'quality'].includes(mode)) return mode
    if (/^(artist|album|genre|playlist|dir):/.test(mode)) return mode
    // Fruehere Ordneransichten (Download-Baum, Mein Computer) → Ordner
    if (mode.startsWith('dl:') && !mode.startsWith('dl:file:')) return 'dir:' + mode.slice(3)
    if (mode.startsWith('fs:')) return 'dir:' + mode.slice(3)
    return null
  }

  let _navRestored = false
  $effect(() => {
    const sections = { fs: secFsOpen, artist: secArtistOpen, album: secAlbumOpen,
                       genre: secGenreOpen, dl: secDlOpen, playlist: secPlaylistOpen,
                       quick: secQuickOpen, dirs: secDirsOpen, filter: secFilterOpen }
    const mode = navMode
    // Waehrend der Suche aufgeklappte Abschnitte sind kein gewollter Zustand.
    if (navQ) return
    // Solange die gemerkte Ansicht noch nicht wiederhergestellt ist, steht
    // navMode auf dem Startwert — der darf die gespeicherte nicht ueberschreiben.
    const saveMode = _navRestored ? (restorableMode(mode) ?? 'all') : (_navSaved.mode ?? 'all')
    try { localStorage.setItem(NAV_KEY, JSON.stringify({ sections, mode: saveMode })) } catch {}
  })

  // Gemerkte Ansicht wiederherstellen, sobald die noetigen Daten da sind.
  // Gibt es das Ziel nicht mehr (Playlist geloescht, Album umbenannt), bleibt
  // es bei "Alle Titel".
  $effect(() => {
    if (_navRestored) return
    const want = restorableMode(_navSaved.mode ?? 'all')
    if (!want || want === 'all') { _navRestored = true; return }
    if (!$connected) return

    if (want.startsWith('playlist:')) {
      if (!$playlists.length) return
      const pl = $playlists.find(x => 'playlist:' + x.path === want)
      _navRestored = true
      if (pl) untrack(() => openPlaylistInLibrary(pl))
      return
    }
    if (want.startsWith('dir:')) {
      if (!$library.length) return
      _navRestored = true
      untrack(() => selectDir(want.slice(4)))
      return
    }
    if (/^(artist|album|genre):/.test(want)) {
      if (!$library.length) return
      const kind = want.slice(0, want.indexOf(':'))
      const val  = want.slice(want.indexOf(':') + 1)
      const list = kind === 'artist' ? artists : kind === 'album' ? albums : genres
      _navRestored = true
      if (list.some(x => x.toLowerCase() === val)) untrack(() => selectNav(want))
      return
    }
    _navRestored = true
    untrack(() => selectNav(want))
  })

  // ── Nav tree search ───────────────────────────────────────────────────────
  let navSearch = $state('')
  const navQ = $derived(navSearch.trim().toLowerCase())
  const filteredArtists = $derived(
    navQ ? artists.filter(a => a.toLowerCase().includes(navQ)) : artists
  )
  const filteredAlbums = $derived(
    navQ ? albums.filter(a => a.toLowerCase().includes(navQ)) : albums
  )
  const filteredGenres = $derived(
    navQ ? genres.filter(g => g.toLowerCase().includes(navQ)) : genres
  )
  const filteredPlaylists = $derived(
    navQ ? $playlists.filter(pl => pl.name.toLowerCase().includes(navQ)) : $playlists
  )
  const filteredDlFolders = $derived(
    navQ && $downloadTreeLoaded
      ? $downloadTree.folders.filter(f => f.name.toLowerCase().includes(navQ))
      : ($downloadTreeLoaded ? $downloadTree.folders : [])
  )
  const filteredDlFiles = $derived.by(() => {
    const raw = navQ && $downloadTreeLoaded
      ? $downloadTree.files.filter(f => f.name.toLowerCase().includes(navQ))
      : ($downloadTreeLoaded ? $downloadTree.files : [])
    const seen = new Set()
    return raw.filter(f => seen.has(f.path) ? false : (seen.add(f.path), true))
  })
  // Abschnitte mit Treffern aufklappen — und nach dem Leeren des Suchfelds
  // wieder in den Zustand von vorher bringen. Frueher blieben sie offen.
  let _preFilterSec = null
  $effect(() => {
    if (!navQ) {
      if (_preFilterSec) {
        const s = _preFilterSec
        _preFilterSec = null
        secArtistOpen = s.artist; secAlbumOpen = s.album; secGenreOpen = s.genre
        secPlaylistOpen = s.playlist; secDlOpen = s.dl
      }
      return
    }
    if (!_preFilterSec) {
      _preFilterSec = untrack(() => ({ artist: secArtistOpen, album: secAlbumOpen, genre: secGenreOpen,
                                       playlist: secPlaylistOpen, dl: secDlOpen }))
    }
    if (filteredArtists.length)  secArtistOpen   = true
    if (filteredAlbums.length)   secAlbumOpen    = true
    if (filteredGenres.length)   secGenreOpen    = true
    if (filteredPlaylists.length) secPlaylistOpen = true
    if (filteredDlFolders.length || filteredDlFiles.length) secDlOpen = true
  })

  // Titelanzahl je Album und Genre, einmal pro Bibliotheksstand berechnet.
  // Dieselben Regeln wie beim Filtern der Titelliste (exakt bzw. zerlegt).
  const albumCounts = $derived.by(() => {
    const m = new Map()
    for (const t of $library) {
      const a = (t.album ?? '').trim().toLowerCase()
      if (a) m.set(a, (m.get(a) ?? 0) + 1)
    }
    return m
  })
  const genreCounts = $derived.by(() => {
    const m = new Map()
    for (const t of $library) {
      for (const part of (t.genre ?? '').split(/[,;/]/)) {
        const g = part.trim().toLowerCase()
        if (g) m.set(g, (m.get(g) ?? 0) + 1)
      }
    }
    return m
  })

  // Zaehler im Abschnittskopf: beim Filtern "Treffer / gesamt"
  const zahl = (gefiltert, gesamt) => navQ ? `${gefiltert} / ${gesamt}` : gesamt
  // ── Favoriten dropdown ────────────────────────────────────────────────────
  let favOpen = $state(false)

  // ── Total download count (all files + all subfolder tracks recursively) ──
  const dlTotalCount = $derived.by(() => {
    if (!$downloadTreeLoaded) return 0
    function countFolder(f) {
      return f.tracks.length + (f.folders ?? []).reduce((s, sub) => s + countFolder(sub), 0)
    }
    return ($downloadTree.files ?? []).length +
           ($downloadTree.folders ?? []).reduce((s, f) => s + countFolder(f), 0)
  })

  // ── Angeheftete Download-Ordner (localStorage-persistent) ────────────────
  let pinnedDlFolders = $state(
    (() => { try { return JSON.parse(localStorage.getItem('synthimix-pinned-dl') ?? '[]') } catch { return [] } })()
  )
  $effect(() => {
    localStorage.setItem('synthimix-pinned-dl', JSON.stringify(pinnedDlFolders))
  })
  function pinDlFolder(folder) {
    if (!pinnedDlFolders.find(p => p.path === folder.path))
      pinnedDlFolders = [...pinnedDlFolders, { name: folder.name, path: folder.path }]
  }
  function unpinDlFolder(path) {
    pinnedDlFolders = pinnedDlFolders.filter(p => p.path !== path)
  }
  function isDlPinned(path) {
    return pinnedDlFolders.some(p => p.path === path)
  }

  // ── A-Z Schnellsprung für Künstler ────────────────────────────────────────
  const AZ_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
  // Getrennt nach Abschnitt, sonst sprang "A" bei den Alben zum ersten
  // Kuenstler mit A, weil die Suche den ganzen Baum durchgeht.
  function jumpLetter(kind, letter) {
    const tree = document.querySelector('.nav-tree')
    const target = tree?.querySelector(`[data-az="${kind}:${letter.toLowerCase()}"]`)
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // ── Download subfolder expand state ──────────────────────────────────────
  let dlExpandedFolders = $state(new Set())
  function toggleDlFolder(path) {
    const s = new Set(dlExpandedFolders)
    s.has(path) ? s.delete(path) : s.add(path)
    dlExpandedFolders = s
  }

  // ── Duplicate scope tracking ──────────────────────────────────────────────
  let dupeSourceMode = $state('all')

  let fsRoots = $state([])
  let fsKids  = $state({})
  let fsOpen  = $state({})
  let fsReady = $state(false)

  async function loadFsRoots() {
    if (!window.electron?.listDir) return
    fsRoots = await window.electron.listDir(null)
    fsReady = true
  }

  async function clickFsNode(node) {
    navMode = 'fs:' + node.path
    search = ''
    selected = new Set()
    if (node.isDir) {
      if (fsOpen[node.path]) {
        fsOpen = { ...fsOpen, [node.path]: false }
      } else {
        if (!fsKids[node.path] && window.electron?.listDir)
          fsKids = { ...fsKids, [node.path]: await window.electron.listDir(node.path) }
        fsOpen = { ...fsOpen, [node.path]: true }
      }
    }
  }

  // ── Ordnerbaum (Schnellzugriff, Musikordner, Computer) ──────────────────
  // Ein Baum, eine Logik: ▸ klappt auf, ein Klick zeigt alle Titel im Ordner
  // samt Unterordnern. Unterordner und Zaehler kommen aus der Bibliothek;
  // Ordner ausserhalb der Bibliothek werden vom Laufwerk gelesen.
  const _nd  = (p) => (p ?? '').replace(/\//g, '\\').replace(/\\+$/, '')
  const _dk  = (p) => _nd(p).toLowerCase()
  const _base = (p) => { const n = _nd(p); return n.split('\\').pop() || n }
  const libDirs = $derived.by(() => {
    const m = new Map()          // Schluessel → { path, name, count, kids:Set }
    for (const t of $library) {
      if (!t.path || t.missing) continue
      const parts = _nd(t.path).split('\\')
      parts.pop()
      let acc = '', parentKey = null
      for (let i = 0; i < parts.length; i++) {
        acc = i === 0 ? parts[0] : acc + '\\' + parts[i]
        const k = acc.toLowerCase()
        let n = m.get(k)
        if (!n) { n = { path: acc, name: parts[i], count: 0, kids: new Set() }; m.set(k, n) }
        n.count++
        if (parentKey) m.get(parentKey).kids.add(k)
        parentKey = k
      }
    }
    return m
  })
  function dirCount(path) { return libDirs.get(_dk(path))?.count ?? 0 }
  // Unterordner: aus der Bibliothek, sonst (Ordner nicht eingelesen) vom Laufwerk
  function dirKids(path) {
    const n = libDirs.get(_dk(path))
    if (n && n.kids.size) {
      return [...n.kids].map(k => libDirs.get(k)).sort((a, b) => a.name.localeCompare(b.name, 'de', { numeric: true }))
    }
    return (fsKids[path] ?? []).filter(c => c.isDir).map(c => ({ path: c.path, name: c.name, count: dirCount(c.path) }))
  }
  function hasKids(path) {
    const n = libDirs.get(_dk(path))
    if (n && n.kids.size) return true
    if (fsKids[path]) return fsKids[path].some(c => c.isDir)
    return !n                   // unbekannt: erst beim Aufklappen nachsehen
  }

  const NAV_OPEN_KEY = 'synthimix-nav-open'
  let dirOpen = $state((() => { try { return new Set(JSON.parse(localStorage.getItem(NAV_OPEN_KEY) ?? '[]')) } catch { return new Set() } })())
  function _saveOpen() { try { localStorage.setItem(NAV_OPEN_KEY, JSON.stringify([...dirOpen])) } catch {} }
  async function _ensureFsKids(path) {
    if (!fsKids[path] && window.electron?.listDir)
      fsKids = { ...fsKids, [path]: await window.electron.listDir(path) }
  }
  async function toggleDir(path, e) {
    e?.stopPropagation()
    const k = _dk(path)
    const s = new Set(dirOpen)
    if (s.has(k)) s.delete(k)
    else {
      s.add(k)
      if (!libDirs.get(k)?.kids.size) await _ensureFsKids(path)
    }
    dirOpen = s
    _saveOpen()
  }
  async function selectDir(path) {
    selectNav('dir:' + path)
    // Ordner ohne Titel in der Bibliothek: Dateien vom Laufwerk zeigen
    if (!dirCount(path)) await _ensureFsKids(path)
  }
  function dirDragStart(e, path) {
    const k = _dk(path) + '\\'
    const tracks = $library.filter(t => _dk(t.path).startsWith(k))
                           .map(t => ({ path: t.path, title: t.title, duration_sec: t.duration_sec ?? 0 }))
    e.dataTransfer.setData('application/x-ytdl-multi', JSON.stringify(tracks))
    e.dataTransfer.effectAllowed = 'copy'
  }

  // Musikordner: die beobachteten Ordner, dazu der Download-Ordner, falls er
  // nicht schon darin liegt
  const musicRoots = $derived.by(() => {
    const roots = ($watchedFolders ?? []).map(w => w.path ?? w).filter(Boolean)
    const dl = $downloadDir
    if (dl && !roots.some(r => _dk(dl) === _dk(r) || _dk(dl).startsWith(_dk(r) + '\\'))) roots.push(dl)
    return roots
  })
  // Suche in der Navigation: passende Ordner als flache Liste
  const matchingDirs = $derived(navQ
    ? [...libDirs.values()].filter(n => n.name.toLowerCase().includes(navQ))
        .sort((a, b) => b.count - a.count).slice(0, 40)
    : [])

  // Schnellzugriff = Favoriten. Frueher angeheftete Download-Ordner einmalig uebernehmen.
  $effect(() => {
    if (!$connected || !pinnedDlFolders.length) return
    untrack(() => {
      for (const p of pinnedDlFolders)
        if (!$favorites.some(f => _dk(f.path) === _dk(p.path))) send({ type: 'add_favorite', path: p.path, name: p.name })
      pinnedDlFolders = []
    })
  })
  async function addQuickFolder(e) {
    e?.stopPropagation()
    const dir = await window.electron?.pickFolder?.()
    if (dir) send({ type: 'add_favorite', path: dir, name: _base(dir) })
  }

  function toggleSection(sec) {
    if (sec === 'fs') {
      secFsOpen = !secFsOpen
      if (secFsOpen && !fsReady) loadFsRoots()
    }
    else if (sec === 'artist')   secArtistOpen = !secArtistOpen
    else if (sec === 'album')    secAlbumOpen  = !secAlbumOpen
    else if (sec === 'genre')    secGenreOpen  = !secGenreOpen
    else if (sec === 'dl') {
      secDlOpen = !secDlOpen
      if (secDlOpen && !$downloadTreeLoaded) loadDlTree()
    }
    else if (sec === 'playlist') secPlaylistOpen = !secPlaylistOpen
    else if (sec === 'quick')    secQuickOpen    = !secQuickOpen
    else if (sec === 'dirs')     secDirsOpen     = !secDirsOpen
    else if (sec === 'filter')   secFilterOpen   = !secFilterOpen
  }

  onMount(() => {
    if (secFsOpen && !fsReady) loadFsRoots()
  })

  function saveQueueAsPlaylist() {
    plNameValue = ''
    plNameDialog = true
  }

  function confirmSavePlaylist() {
    if (plNameValue.trim()) send({ type: 'save_playlist', name: plNameValue.trim() })
    plNameDialog = false
  }

  function openPlaylistInLibrary(pl) {
    // Request content if not cached yet, then show in library view
    if (!$playlistContent[pl.path]) {
      send({ type: 'get_playlist_content', path: pl.path })
    }
    selectNav('playlist:' + pl.path)
  }

  function loadPlaylistToQueue(path) {
    send({ type: 'load_playlist', path })
  }

  function deletePlaylist(path) {
    plDeletePath = path
  }

  // ── Drag library track → playlist in nav ──────────────────────────────────
  let dragOverPlaylist = $state(null)   // playlist path being hovered

  function dropOnPlaylist(e, plPath) {
    e.preventDefault()
    dragOverPlaylist = null
    let added = false

    function addTrack(t) {
      added = true
      send({ type: 'playlist_add_track', playlist: plPath,
             path: t.path, title: t.title, duration_sec: t.duration_sec ?? 0 })
    }

    const multiRaw = e.dataTransfer.getData('application/x-ytdl-multi')
    if (multiRaw) {
      try { JSON.parse(multiRaw).forEach(addTrack) } catch {}
    } else {
      const rich = e.dataTransfer.getData('application/x-ytdl-track')
      if (rich) {
        try { addTrack(JSON.parse(rich)) } catch {}
      } else {
        const path = e.dataTransfer.getData('text/plain')
        if (path) {
          const t = $library.find(lt => lt.path === path)
          if (t) addTrack(t)
        }
      }
    }
    // Invalidate cache so playlist content refreshes after drop
    if (added) playlistContent.update(m => { const c = {...m}; delete c[plPath]; return c })
  }

  // ── Track list filtered + sorted ──────────────────────────────────────────
  const filtered = $derived.by(() => {
    const q = search.toLowerCase()

    // ── Playlist mode: return playlist tracks directly (in playlist order) ──
    if (navMode.startsWith('playlist:')) {
      const plPath = navMode.slice(9)
      const plTracks = $playlistContent[plPath]
      if (!plTracks) return []
      const libByPath = new Map($library.map(t => [t.path, t]))
      let list = plTracks.map(pt => libByPath.get(pt.path) ?? pt)
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      // Frueher immer in Playlist-Reihenfolge — Spaltenklicks taten nichts
      return dlColSort ? applySort(list) : list
    }

    // Gemeinsame Sort-Funktion für alle Modi
    function applySort(list) {
      if (navMode === 'recent') {
        return list.sort((a, b) => (b.play_count ?? 0) - (a.play_count ?? 0))
      }
      return [...list].sort((a, b) => {
        let av, bv
        if (sortCol === 'artist') {
          av = getTrackArtistTitle(a).artist.toLowerCase()
          bv = getTrackArtistTitle(b).artist.toLowerCase()
        } else if (sortCol === 'title') {
          av = getTrackArtistTitle(a).title.toLowerCase()
          bv = getTrackArtistTitle(b).title.toLowerCase()
        } else if (sortCol === 'key') {
          // Nach Camelot statt alphabetisch: passende Tonarten stehen beieinander.
          // Titel ohne Tonart immer ans Ende — sonst stehen beim Umdrehen
          // tausende leere Zellen oben.
          if (!a.key !== !b.key) return a.key ? -1 : 1
          av = keySortValue(a.key)
          bv = keySortValue(b.key)
        } else {
          const key = sortCol === 'duration' ? 'duration_sec'
                    : sortCol === 'bitrate'  ? 'bitrate_kbps'
                    : sortCol
          av = a[key] ?? ''
          bv = b[key] ?? ''
        }
        return sortAsc
          ? (av < bv ? -1 : av > bv ? 1 : 0)
          : (av > bv ? -1 : av < bv ? 1 : 0)
      })
    }

    // ── Downloads: Verlauf und Download-Ordner in einem, neueste zuerst ─────
    // Aus dem Verlauf kommt das Ladedatum, aus dem Ordner alles, was vor dem
    // Verlauf oder von Hand dort gelandet ist. Geloeschte Dateien bleiben
    // ausgegraut stehen und lassen sich neu laden.
    if (navMode === 'downloads') {
      const libByPath = new Map($library.map(t => [t.path, t]))
      const seen = new Set()
      let list = []
      $dlHistory.forEach((h, i) => {
        if (!h.path || seen.has(h.path)) return
        seen.add(h.path)
        const t = libByPath.get(h.path) ?? {
          path: h.path, title: h.title || h.path.split(/[\\/]/).pop(),
          artist: '', duration_sec: 0, lufs: -99, bpm: 0, bitrate_kbps: h.bitrate_kbps ?? 0
        }
        // Aeltere Eintraege kennen nur den Tag: dann die Reihenfolge des
        // Verlaufs (neueste zuerst) innerhalb des Tages beibehalten
        const day = h.date ? Date.parse(h.date + 'T00:00:00') / 1000 : 0
        const ts  = h.ts || (day ? day + 86399 - i : 0)
        list.push({ ...t, _dlTs: ts, _gone: !!h.gone, _url: h.url || '', missing: t.missing || !!h.gone })
      })
      if ($downloadTreeLoaded) {
        const add = (f) => {
          if (seen.has(f.path)) return
          seen.add(f.path)
          const t = libByPath.get(f.path) ?? { path: f.path, title: f.name, artist: '', duration_sec: 0, lufs: -99, bpm: 0 }
          list.push({ ...t, _dlTs: Math.floor(f.mtime ?? 0), _gone: false, _url: '' })
        }
        const walk = (folders) => { for (const fo of folders ?? []) { (fo.tracks ?? []).forEach(add); walk(fo.folders) } }
        ;($downloadTree.files ?? []).forEach(add)
        walk($downloadTree.folders)
      }
      list.sort((a, b) => (b._dlTs ?? 0) - (a._dlTs ?? 0))
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      return dlColSort ? applySort(list) : list
    }

    // ── dl_all: all downloaded files (root + all subfolders recursively) ───
    if (navMode === 'dl_all') {
      if (!$downloadTreeLoaded) return []
      const libByPath = new Map($library.map(t => [t.path, t]))
      const make = (f) => libByPath.get(f.path) ?? { path: f.path, title: f.name, artist: '', duration_sec: 0, lufs: -99, bpm: 0 }
      function gatherFolder(folders) {
        let r = []
        for (const folder of folders) {
          r.push(...folder.tracks.map(make))
          if (folder.folders?.length) r.push(...gatherFolder(folder.folders))
        }
        return r
      }
      let list = [
        ...($downloadTree.files ?? []).map(make),
        ...gatherFolder($downloadTree.folders ?? [])
      ]
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      return applySort(list)
    }

    // ── dl: modes — show tracks from download tree (may not be in library) ──
    if (navMode.startsWith('dl:file:')) {
      const filePath = navMode.slice(8)
      const file = $downloadTree.files.find(f => f.path === filePath)
      if (!file) return []
      const libEntry = $library.find(lt => lt.path === filePath)
      return [libEntry ?? { path: filePath, title: file.name, artist: '', duration_sec: 0, lufs: -99, bpm: 0 }]
    }
    if (navMode.startsWith('dl:')) {
      const folderPath = navMode.slice(3)
      // Rekursiv suchen: Unterordner (auch angeheftete) zeigten sonst eine
      // Zahl im Baum, aber eine leere Liste.
      const findFolder = (list) => {
        for (const f of list ?? []) {
          if (f.path === folderPath) return f
          const sub = findFolder(f.folders)
          if (sub) return sub
        }
        return null
      }
      const folder = findFolder($downloadTree.folders)
      if (!folder) return []
      const libByPath = new Map($library.map(t => [t.path, t]))
      let list = folder.tracks.map(f =>
        libByPath.get(f.path) ?? { path: f.path, title: f.name, artist: '', duration_sec: 0, lufs: -99, bpm: 0 }
      )
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      return applySort(list)
    }

    // ── Ordner: alle Titel darin samt Unterordnern ─────────────────────────
    if (navMode.startsWith('dir:')) {
      const dirPath = navMode.slice(4)
      const k = _dk(dirPath) + '\\'
      let list = $library.filter(t => t.path && _dk(t.path).startsWith(k))
      if (!list.length) {
        // Nicht in der Bibliothek: Dateien direkt im Ordner vom Laufwerk
        const AUDIO = /\.(mp3|flac|wav|m4a|ogg|aac|opus|wma|aiff?)$/i
        list = (fsKids[dirPath] ?? []).filter(c => !c.isDir && AUDIO.test(c.name)).map(f => ({
          path: f.path, title: f.name.replace(/\.[^.]+$/, ''), artist: '', duration_sec: 0, lufs: -99, bpm: 0, bitrate_kbps: 0
        }))
      }
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      return applySort(list)
    }

    // ── Filesystem browser: show audio files from that directory ─────────────
    if (navMode.startsWith('fs:')) {
      const fsPath = navMode.slice(3)
      const AUDIO = /\.(mp3|flac|wav|m4a|ogg|aac|opus|wma)$/i
      const kids = fsKids[fsPath] ?? []
      const audioFiles = kids.filter(k => !k.isDir && AUDIO.test(k.name))
      const libByPath = new Map($library.map(t => [t.path, t]))
      let list = audioFiles.map(f =>
        libByPath.get(f.path) ?? {
          path: f.path, title: f.name.replace(/\.[^.]+$/, ''),
          artist: '', duration_sec: 0, lufs: -99, bpm: 0, bitrate_kbps: 0
        }
      )
      if (q) list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) || artist.toLowerCase().includes(q)
      })
      return applySort(list)
    }

    let list = $library.filter(t => {
      if (navMode === 'recent')          return (t.play_count ?? 0) > 0
      if (navMode.startsWith('folder:')) return t.folder === navMode.slice(7)
      if (navMode.startsWith('artist:')) {
        const target = navMode.slice(7).toLowerCase()
        return getTrackArtistTitle(t).artist.toLowerCase().includes(target)
      }
      if (navMode.startsWith('album:')) {
        const target = navMode.slice(6).toLowerCase()
        return (t.album ?? '').toLowerCase() === target
      }
      if (navMode.startsWith('genre:')) {
        const target = navMode.slice(6).toLowerCase()
        return (t.genre ?? '').split(/[,;/]/).map(s => s.trim().toLowerCase()).includes(target)
      }
      if (navMode === 'duplicates') return dupesAll.has(t.path)
      if (navMode === 'quality') {
        if (qualityFilter === 'ignored') return qualityInfo.ignored.has(t.path)
        const r = qualityInfo.map.get(t.path)
        return !!r && (qualityFilter === 'all' || r.includes(qualityFilter))
      }
      if (navMode === 'all' && linkHidden.has(t.path)) return false
      if (navMode === 'all' && hideDupesAuto) return !globalDupes.hidden.has(t.path)
      return true
    })

    if (q) {
      const qKey = q.replace('#', '♯')
      list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) ||
               artist.toLowerCase().includes(q) ||
               (t.folder ?? '').toLowerCase().includes(q) ||
               (t.key ?? '').toLowerCase() === qKey
      })
    }

    const sorted = applySort(list)
    return navMode === 'duplicates' ? groupDupes(sorted) : sorted
  })

  let hideDupesAuto = $state(true)

  // Stems are excluded from duplicate detection entirely
  const STEM_RE = /\b(stem|stems|vocal|vocals|instrumental|karaoke|acapella|a[\s-]cappella)\b/i

  // tracks to scan for dupes — scoped to current folder/artist/all
  const dupeScopeTracks = $derived.by(() => {
    const mode = dupeSourceMode
    if (mode === 'all' || mode === 'duplicates') return $library
    if (mode.startsWith('fs:')) {
      const p = mode.slice(3)
      return $library.filter(t => t.path.startsWith(p + '\\') || t.path.startsWith(p + '/'))
    }
    if (mode.startsWith('artist:')) {
      const target = mode.slice(7).toLowerCase()
      return $library.filter(t => getTrackArtistTitle(t).artist.toLowerCase().includes(target))
    }
    if (mode.startsWith('playlist:')) {
      const plPath = mode.slice(9)
      return $playlistContent[plPath] ?? []
    }
    return $library
  })

  // Vorher gab es nur die Berechnung fuer den zuletzt geprueften Bereich. Nach
  // einem Ordner-Scan blendete "Alle Titel" dann nur noch dessen Kopien aus,
  // und die Zahl links sprang je nach Ansicht.
  // Zusaetze, die nichts ueber die Aufnahme sagen: "(Official Video)", "[Lyrics]",
  // "(Visualizer)" … Remix, Edit, VIP oder Extended bleiben stehen — das sind
  // wirklich andere Versionen.
  const DUPE_NOISE_RE = /[([][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|clip|videoclip|free download|out now)\b[^)\]]*[)\]]/gi
  function dupeNorm(s) {
    return s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')   // Ü → u
      .replace(/&/g, ' and ').replace(/\b(ft|feat|featuring)\b\.?/g, 'feat')
      .replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()
  }
  // Kuenstler kommt zuerst aus "Kuenstler - Titel": das Kuenstler-Tag ist bei
  // YouTube-Downloads meist der Kanal ("UKF Drum & Bass"), dadurch wurden
  // gleiche Songs von verschiedenen Kanaelen nicht erkannt.
  function dupeKey(t) {
    const title = (t.title ?? '').replace(DUPE_NOISE_RE, '').replace(/^\[[^\]]*\]\s*/, '').trim()
    const m = title.match(/^(.+?)\s+[-–—]\s+(.+)$/)
    const artist = m ? m[1] : ((t.artist ?? '').trim() || (t.album_artist ?? '').trim())
    const key = dupeNorm(artist) + '|' + dupeNorm(m ? m[2] : title)
    return key.replace('|', '').length >= 3 ? key : null
  }
  // Gleicher Name reicht nicht: nur Dateien, deren Laenge hoechstens so weit
  // abweicht, sind dieselbe Aufnahme. Vorher landeten 0:14 und 6:49 in einer Gruppe.
  const DUPE_DUR_TOL = 3

  // "Beste Datei" nach tatsaechlicher Qualitaet statt nur nach Bitrate: eine WAV,
  // die ein YouTube-Konverter geschrieben hat, ist nicht besser als ihre Quelle.
  const LOSSLESS_EXT = new Set(['wav', 'flac', 'aiff', 'aif', 'alac'])
  const STREAM_MARK_RE = /dvdvideosoft|youtube|youtu\.be|y2mate|ytmp3|savefrom|soundcloud/i
  function dupeQuality(t) {
    const kbps = t.bitrate_kbps ?? 0
    const fromStream = STREAM_MARK_RE.test(t.comment ?? '') || /_soundcloud\./i.test(t.path ?? '')
    if (LOSSLESS_EXT.has((t.ext ?? '').toLowerCase().replace('.', '')) && !fromStream) return 10000 + kbps
    return Math.min(kbps, fromStream ? 160 : 320)   // YouTube liefert hoechstens ~160 kbps
  }
  function isLossless(t) { return LOSSLESS_EXT.has((t.ext ?? '').toLowerCase().replace('.', '')) }

  // ── Qualitaet ─────────────────────────────────────────────────────────────
  // Drei Gruende: Musikvideo-Version (Intro, Pausen), echte niedrige Bitrate,
  // und hochgerechnet — die Datei behauptet viel kbps, schneidet die Hoehen
  // aber wie eine 128er-Quelle ab (Messung im Backend, Feld cutoff_khz).
  const QUALITY_MIN_SEC = 60                 // Samples und FX nicht bewerten
  const CUTOFF_UPSCALED = 17.0               // kHz, wie _CUTOFF_UPSCALED_KHZ im Backend
  const MV_TITLE_RE = /\b(official\s+(?:music\s+)?video|music\s+video|official\s+mv|mv)\b/i
  const QUALITY_LABELS = { video: 'Musikvideo', bitrate: 'Niedrige Bitrate', upscaled: 'Hochgerechnet' }
  // Warnfarbe: rot erst, wenn die Hoehen unter 16 kHz abgeschnitten sind
  // (deutlich hoerbar), alles andere orange
  const CUTOFF_RED = 16
  const QUALITY_TIPS = {
    video: 'Musikvideo-Version: oft Intro, Pausen oder Geräusche',
    bitrate: 'Unter 192 kbps',
    upscaled: 'Höhen enden früh — klingt wie höchstens 128 kbps, egal was die Datei angibt',
  }
  function qualityReasons(t) {
    if (t.missing || (t.duration_sec ?? 0) < QUALITY_MIN_SEC || STEM_RE.test(t.title ?? '')) return []
    const r = []
    if (MV_TITLE_RE.test(t.title ?? '')) r.push('video')
    const kbps = t.bitrate_kbps ?? 0
    if (!isLossless(t) && kbps > 0 && kbps < 192) r.push('bitrate')
    else if ((t.cutoff_khz ?? 0) > 5 && t.cutoff_khz < CUTOFF_UPSCALED) r.push('upscaled')
    return r
  }
  const qualityInfo = $derived.by(() => {
    const map = new Map()
    const ignored = new Map()      // "Passt so" — ausgeblendet, aber wieder abrufbar
    const counts = { video: 0, bitrate: 0, upscaled: 0 }
    let measured = 0
    for (const t of $library) {
      if (t.cutoff_khz != null) measured++
      const r = qualityReasons(t)
      if (!r.length) continue
      if (t.quality_ok) { ignored.set(t.path, r); continue }
      map.set(t.path, r)
      for (const k of r) counts[k]++
    }
    return { map, ignored, counts, measured }
  })
  function qualityOk(track, ok) { send({ type: 'quality_ok', path: track.path, ok }) }
  let qualityFilter = $state('all')

  // ── Ordner, in denen Kopien erlaubt sind ─────────────────────────────────
  // Eine Kopie dort gilt nicht als Duplikat des Originals woanders (z. B.
  // Event-Ordner "Hochzeit" mit Titeln aus den Genre-Ordnern). Zwei Kopien im
  // selben erlaubten Ordner werden weiter gemeldet.
  const _normDir = (p) => (p ?? '').replace(/\//g, '\\').replace(/\\$/, '').toLowerCase()
  const allowDirs = $derived(($appSettings.dupeAllowFolders ?? []).map(_normDir))
  // Jeder direkte Unterordner eines erlaubten Ordners ist ein eigener Bereich
  // (z. B. Playlists\\Party und Playlists\\Hochzeit): derselbe Song in zwei
  // Unterordnern ist okay, zweimal im selben wird gemeldet.
  function allowedZone(path) {
    const pn = _normDir(path)
    const root = allowDirs.find(f => pn.startsWith(f + '\\'))
    if (!root) return ''
    const rest = pn.slice(root.length + 1)
    const i = rest.indexOf('\\')
    return i < 0 ? root : root + '\\' + rest.slice(0, i)
  }
  function allowedFolderOf(path) {
    const pn = _normDir(path)
    return ($appSettings.dupeAllowFolders ?? []).find(f => pn === _normDir(f) || pn.startsWith(_normDir(f) + '\\')) ?? null
  }
  function setDupeAllowed(folder, on) {
    folderCtx = null; closeCtx()
    appSettings.update(s => {
      const cur = s.dupeAllowFolders ?? []
      const rest = cur.filter(f => _normDir(f) !== _normDir(folder))
      return { ...s, dupeAllowFolders: on ? [...rest, folder] : rest }
    })
  }

  // Verknuepfte Dateien (Hardlinks aus "Kompletter Ordner"): dieselbe Datei
  // unter mehreren Pfaden. Die erste in der Bibliothek zaehlt, die weiteren
  // sind nie ein Duplikat und stehen unter "Alle Titel" nicht noch einmal.
  const linkHidden = $derived.by(() => {
    const first = new Map(), hidden = new Set()
    for (const t of $library) {
      if (!t.fid) continue
      if (first.has(t.fid)) hidden.add(t.path)
      else first.set(t.fid, t.path)
    }
    return hidden
  })

  function findDupes(tracks) {
    const byKey = new Map()  // normalized key → track[]

    for (const t of tracks) {
      if (STEM_RE.test(t.title ?? '')) continue   // skip stems
      if (linkHidden.has(t.path)) continue        // Verknuepfung, dieselbe Datei
      const key = dupeKey(t)
      if (!key) continue
      if (!byKey.has(key)) byKey.set(key, [])
      byKey.get(key).push(t)
    }

    const hidden = new Set()
    const all    = new Set()
    const groupsList = []  // for duplicate-scan dialog

    for (const same of byKey.values()) {
      if (same.length < 2) continue
      // nach Laenge aufteilen
      const byDur = [...same].sort((a, b) => (a.duration_sec ?? 0) - (b.duration_sec ?? 0))
      const clusters = [[byDur[0]]]
      for (const t of byDur.slice(1)) {
        const c = clusters.at(-1)
        if (Math.abs((t.duration_sec ?? 0) - (c[0].duration_sec ?? 0)) <= DUPE_DUR_TOL) c.push(t)
        else clusters.push([t])
      }
      // Erlaubte Ordner bilden je eine eigene Zone
      const zoned = []
      for (const c of clusters) {
        if (c.length < 2) continue
        if (!allowDirs.length) { zoned.push(c); continue }
        const byZone = new Map()
        for (const t of c) {
          const z = allowedZone(t.path)
          if (!byZone.has(z)) byZone.set(z, [])
          byZone.get(z).push(t)
        }
        zoned.push(...byZone.values())
      }
      for (const group of zoned) {
        if (group.length < 2) continue
        const sorted = [...group].sort((a, b) =>
          dupeQuality(b) - dupeQuality(a) ||
          (isLossless(a) ? 1 : 0) - (isLossless(b) ? 1 : 0) ||   // gleich gut: kleinere Datei
          (b.bitrate_kbps ?? 0) - (a.bitrate_kbps ?? 0) ||
          (b.duration_sec ?? 0) - (a.duration_sec ?? 0)
        )
        sorted.forEach((t, i) => {
          all.add(t.path)
          if (i > 0) hidden.add(t.path)
        })
        groupsList.push({ best: sorted[0], others: sorted.slice(1) })
      }
    }

    return { hidden, all, groups: groupsList }
  }

  // Welche Datei einer Gruppe bleibt: automatisch die beste, per "Behalten"
  // in der Zeile aenderbar (gemerkt je Gruppe, Schluessel = kleinster Pfad)
  const KEEP_KEY = 'synthimix-dupe-keep'
  let dupeKeep = $state((() => { try { return JSON.parse(localStorage.getItem(KEEP_KEY) ?? '{}') } catch { return {} } })())
  function withKeep(d) {
    const groups = d.groups.map(g => {
      const members = [g.best, ...g.others]
      const gid = members.map(t => t.path).sort()[0]
      const keeper = members.find(t => t.path === dupeKeep[gid]) ?? g.best
      return { gid, best: keeper, others: members.filter(t => t !== keeper) }
    })
    return { all: d.all, groups, hidden: new Set(groups.flatMap(g => g.others.map(t => t.path))) }
  }
  function keepDupe(track) {
    const g = dupeGroupOf.get(track.path)
    if (!g) return
    dupeKeep = { ...dupeKeep, [g.gid]: track.path }
    try { localStorage.setItem(KEEP_KEY, JSON.stringify(dupeKeep)) } catch {}
  }
  function trashOne(track) { trashPaths([track.path]) }
  // In den Papierkorb: Zeilen sofort ausblenden, das Backend erledigt alle
  // Dateien in einem Rutsch und schickt danach die Bibliothek (misslungene
  // Titel tauchen dann wieder auf, mit Hinweis unten links)
  function trashPaths(paths) {
    const set = new Set(paths)
    if (!set.size) return
    library.update(l => l.filter(t => !set.has(t.path)))
    downloadTree.update(dt => ({ folders: (dt.folders ?? []).map(f => ({ ...f, tracks: (f.tracks ?? []).filter(t => !set.has(t.path)) })),
                                  files: (dt.files ?? []).filter(f => !set.has(f.path)) }))
    const sel = new Set(selected); for (const p of set) sel.delete(p); selected = sel
    send({ type: 'library_remove_disk_many', paths: [...set] })
  }

  const globalDupes = $derived(withKeep(findDupes($library)))
  const scopedDupes = $derived(dupeSourceMode === 'all' || dupeSourceMode === 'duplicates'
                               ? globalDupes : withKeep(findDupes(dupeScopeTracks)))
  const dupesHidden = $derived(scopedDupes.hidden)   // schlechtere Kopien
  const dupesAll    = $derived(scopedDupes.all)      // alle Dateien in Gruppen
  const dupesGroups = $derived(scopedDupes.groups)   // je Song eine Gruppe
  const dupeGroupOf = $derived(new Map(dupesGroups.flatMap(g => [g.best, ...g.others].map(t => [t.path, g]))))
  // Duplikat-Ansicht: Gruppen zusammenhalten, die bleibende Datei zuerst
  function groupDupes(list) {
    const inList = new Set(list.map(t => t.path))
    const out = [], seen = new Set()
    for (const t of list) {
      const g = dupeGroupOf.get(t.path)
      if (!g) { out.push(t); continue }
      if (seen.has(g.gid)) continue
      seen.add(g.gid)
      for (const m of [g.best, ...g.others]) if (inList.has(m.path)) out.push(m)
    }
    return out
  }
  const dupeGrpStart = $derived.by(() => {
    const s = new Set()
    if (navMode !== 'duplicates') return s
    let last = null
    for (const t of filtered) {
      const gid = dupeGroupOf.get(t.path)?.gid
      if (gid !== last && last !== null) s.add(t.path)
      last = gid
    }
    return s
  })

  // Wo gerade geprueft wird, fuer den Satz oben in der Duplikat-Ansicht
  const dupeScopeLabel = $derived.by(() => {
    const m = dupeSourceMode
    if (m.startsWith('fs:'))       return ` im Ordner „${m.slice(3).split(/[\\/]/).filter(Boolean).pop()}“`
    if (m.startsWith('artist:'))   return ` bei „${m.slice(7)}“`
    if (m.startsWith('playlist:')) return ' in dieser Playlist'
    return ''
  })

  // ── Playlist duplicate detection ──────────────────────────────────────────
  const plDupesGroups = $derived.by(() => {
    if (!navMode.startsWith('playlist:')) return []
    const plPath = navMode.slice(9)
    const tracks = $playlistContent[plPath]
    if (!tracks || tracks.length < 2) return []

    const groups = new Map()
    for (const t of tracks) {
      if (STEM_RE.test(t.title ?? '')) continue
      const artist = (t.artist ?? '').trim() ||
        (t.album_artist ?? '').trim() ||
        ((t.title ?? '').match(/^(.+?)\s+[-–—]\s+.+$/)?.[1]?.trim() ?? '')
      const songTitle = artist
        ? (t.title ?? '').replace(/^.+?\s+[-–—]\s+/, '').trim()
        : (t.title ?? '').trim()
      const key = (artist.toLowerCase() + '|' + songTitle.toLowerCase())
        .replace(/\s+/g, ' ').replace(/[^\w\s|]/g, '').trim()
      if (key.replace(/[|]/g, '').length < 3) continue
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(t)
    }

    const groupsList = []
    for (const tracks of groups.values()) {
      if (tracks.length < 2) continue
      const sorted = [...tracks].sort((a, b) =>
        (b.bitrate_kbps ?? 0) - (a.bitrate_kbps ?? 0) ||
        (b.duration_sec ?? 0) - (a.duration_sec ?? 0)
      )
      groupsList.push({ best: sorted[0], others: sorted.slice(1) })
    }
    return groupsList
  })


  // Downloads stehen nach Ladedatum; erst ein Klick auf eine Spalte sortiert um
  let dlColSort = $state(false)
  // Wiedergabe-Verlauf und Downloads haben ihre eigene Reihenfolge
  const ownOrder  = $derived(navMode === 'downloads' || navMode.startsWith('playlist:'))
  const sortShown = $derived(navMode !== 'recent' && (!ownOrder || dlColSort))
  // Position in der Playlist (Spalte "#")
  const playlistPos = $derived.by(() => {
    if (!navMode.startsWith('playlist:')) return new Map()
    return new Map(($playlistContent[navMode.slice(9)] ?? []).map((pt, i) => [pt.path, i + 1]))
  })
  function setSort(key) {
    if (ownOrder && !dlColSort) { dlColSort = true; sortCol = key; sortAsc = key === 'title' || key === 'key'; return }
    if (sortCol === key) sortAsc = !sortAsc
    else { sortCol = key; sortAsc = key === 'title' || key === 'key' }   // Tonart beginnt bei 1A
  }

  // ── Column system (widths, visibility, order — all persisted) ───────────
  const ALL_COL_DEFS = [
    { key: 'title',        label: 'Titel'            },
    { key: 'artist',       label: 'Künstler'         },
    { key: 'album',        label: 'Album'            },
    { key: 'genre',        label: 'Genre'            },
    { key: 'album_artist', label: 'Albumkünstler'    },
    { key: 'folder',       label: 'Ordner'           },
    { key: 'ext',          label: 'Format'           },
    { key: 'duration',     label: 'Dauer'            },
    { key: 'lufs',         label: 'LUFS'             },
    { key: 'bpm',          label: 'BPM'              },
    { key: 'key',          label: 'Tonart'           },
    { key: 'energy',       label: 'Energie'          },
    { key: 'bitrate',      label: 'kbps'             },
    { key: 'comment',      label: 'Kanal'            },
    { key: 'mtime',        label: 'Geändert'         },
  ]
  const COL_DEFAULTS  = { title: 220, artist: 140, album: 130, genre: 100, album_artist: 120, folder: 110, ext: 60, duration: 64, lufs: 60, bpm: 56, key: 64, energy: 64, bitrate: 60, comment: 110, mtime: 84 }
  // Mindestbreiten, damit Spaltenkoepfe (11px, Grossbuchstaben) und Tonart-Chips
  // nicht abgeschnitten werden — gilt auch fuer frueher gespeicherte, schmalere Breiten
  const COL_MIN = { ext: 60, duration: 64, lufs: 58, bpm: 52, key: 64, energy: 64, bitrate: 56, mtime: 80 }
  const COL_VIS_DEF   = { title: true, artist: true, album: false, genre: false, album_artist: false, folder: false, ext: false, duration: true, lufs: true, bpm: true, key: true, energy: false, bitrate: true, comment: false, mtime: false }
  const COL_ORDER_DEF = ALL_COL_DEFS.map(c => c.key)

  function _loadColState() {
    try { return JSON.parse(localStorage.getItem('libColState') || '{}') } catch { return {} }
  }
  const _cs = _loadColState()
  let colWidths  = $state(Object.fromEntries(Object.entries({ ...COL_DEFAULTS, ...(_cs.widths || {}) })
                            .map(([k, w]) => [k, Math.max(w, COL_MIN[k] ?? 28)])))
  let colVisible = $state({ ...COL_VIS_DEF,   ...(_cs.visible || {}) })
  // Gespeicherte Reihenfolge laden, fehlende neue Spalten am Ende anhängen
  // Neue Spalten landen hinter ihrem Vorgaenger aus der Standardreihenfolge
  // statt ganz am Ende — "Tonart" soll neben "BPM" auftauchen.
  const _savedOrder = Array.isArray(_cs.order) ? [..._cs.order] : [...COL_ORDER_DEF]
  for (const [i, k] of COL_ORDER_DEF.entries()) {
    if (_savedOrder.includes(k)) continue
    const prev = COL_ORDER_DEF.slice(0, i).reverse().find(p => _savedOrder.includes(p))
    _savedOrder.splice(prev ? _savedOrder.indexOf(prev) + 1 : 0, 0, k)
  }
  let colOrder   = $state(_savedOrder)
  const cols     = $derived(colOrder.map(k => ALL_COL_DEFS.find(c => c.key === k)).filter(c => c && colVisible[c.key]))

  function saveColState() {
    try { localStorage.setItem('libColState', JSON.stringify({ widths: colWidths, visible: colVisible, order: colOrder })) } catch {}
  }
  // Legacy key cleanup
  try { localStorage.removeItem('libColWidths') } catch {}

  let colPickerOpen = $state(false)

  let _colResize = null
  function startColResize(e, key) {
    e.preventDefault(); e.stopPropagation()
    _colResize = { key, startX: e.clientX, startW: colWidths[key] }
    const move = (ev) => { if (!_colResize) return; colWidths[_colResize.key] = Math.max(28, _colResize.startW + ev.clientX - _colResize.startX) }
    const up   = () => { window.removeEventListener('mousemove', move); _colResize = null; saveColState() }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up, { once: true })
  }

  // Column drag-to-reorder
  let _colDragKey = null
  function onColDragStart(e, key) { _colDragKey = key; e.dataTransfer.effectAllowed = 'move' }
  function onColDragOver(e, key)  { if (_colDragKey && _colDragKey !== key) e.preventDefault() }
  function onColDrop(e, key) {
    e.preventDefault()
    if (!_colDragKey || _colDragKey === key) return
    const from = colOrder.indexOf(_colDragKey)
    const to   = colOrder.indexOf(key)
    if (from < 0 || to < 0) return
    const o = [...colOrder]; o.splice(from, 1); o.splice(to, 0, _colDragKey)
    colOrder = o; _colDragKey = null; saveColState()
  }

  // Scroll sync: header follows rows horizontally
  let _colHeaderEl = $state(null)
  let _rowsEl      = $state(null)
  // Titel-Spalte nimmt freien Platz rechts mit (sonst schnitt sie bei 220 px
  // ab, waehrend daneben Platz leer blieb). Zusatzbreite nur fuer die Anzeige,
  // die eingestellte Spaltenbreite bleibt.
  let titleExtra = $state(0)
  function fitTitle() {
    if (!_colHeaderEl || !_rowsEl) return
    if (!cols.some(c => c.key === 'title')) { if (titleExtra) titleExtra = 0; return }
    let sum = 0
    for (const ch of _colHeaderEl.children) sum += ch.offsetWidth
    const natural = sum - titleExtra
    const avail = Math.min(_rowsEl.clientWidth, _colHeaderEl.clientWidth)
    const ex = Math.max(0, Math.floor(avail - natural))
    if (ex !== titleExtra) titleExtra = ex
  }
  $effect(() => {
    if (!_rowsEl || !_colHeaderEl) return
    const ro = new ResizeObserver(() => fitTitle())
    ro.observe(_rowsEl); ro.observe(_colHeaderEl)
    return () => ro.disconnect()
  })
  $effect(() => { void cols; void JSON.stringify(colWidths); void navMode; tick().then(fitTitle) })
  function onRowsScroll() { if (_colHeaderEl && _rowsEl) _colHeaderEl.scrollLeft = _rowsEl.scrollLeft; qTip = null }

  // Hinweis zum Warnzeichen: frei schwebend, damit ihn Tabellenrand und
  // Scrollbereich nicht abschneiden
  let qTip = $state(null)   // { x, y, track, reasons }
  function showQTip(e, track) {
    const r = e.currentTarget.getBoundingClientRect()
    qTip = { x: Math.min(r.right + 6, window.innerWidth - 356), y: r.top + r.height / 2,
             track, reasons: qualityInfo.map.get(track.path) ?? qualityInfo.ignored.get(track.path) ?? [] }
  }

  // Virtual scrolling
  // Zeilenhoehe folgt der Dichte (lib/ui.css: --row-h) — die virtuelle
  // Liste muss dieselbe Hoehe rechnen, sonst springt das Scrollen
  const ROW_H  = $derived($density === 'comfortable' ? 40 : 28)

  // Sprung zu einem Titel (z. B. "Zeigen" in der Download-Rueckfrage): Alle Titel,
  // Suche leeren, ausgeblendete Kopie notfalls einblenden, markieren, hinscrollen.
  $effect(() => {
    const p = $revealPath
    if (!p) return
    untrack(() => {
      selectNav('all')
      if (globalDupes.hidden.has(p)) hideDupesAuto = false
      selected = new Set([p])
      selectionOwner.set('library')
      tick().then(() => {
        const idx = filtered.findIndex(t => t.path === p)
        if (idx >= 0 && _rowsEl) _rowsEl.scrollTop = Math.max(0, idx * ROW_H - _rowsEl.clientHeight / 2)
      })
    })
    revealPath.set(null)
  })
  const BUFFER = 30
  let _scrollTop  = $state(0)
  let _clientH    = $state(600)

  $effect(() => {
    if (!_rowsEl) return
    const el = _rowsEl
    const onScroll = () => { _scrollTop = el.scrollTop }
    const onResize = () => { _clientH = el.clientHeight }
    el.addEventListener('scroll', onScroll, { passive: true })
    const ro = new ResizeObserver(onResize)
    ro.observe(el)
    _clientH = el.clientHeight
    return () => { el.removeEventListener('scroll', onScroll); ro.disconnect() }
  })

  const _vStart = $derived(Math.max(0, Math.floor(_scrollTop / ROW_H) - BUFFER))
  const _vEnd   = $derived(Math.min(filtered.length, Math.ceil((_scrollTop + _clientH) / ROW_H) + BUFFER))
  const _vItems = $derived(filtered.slice(_vStart, _vEnd))

  function fmtMtime(ts) {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    const dd = d.getDate().toString().padStart(2,'0')
    const mm = (d.getMonth()+1).toString().padStart(2,'0')
    const yy = d.getFullYear().toString().slice(2)
    return `${dd}.${mm}.${yy}`
  }

  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }

  function getTrackArtistTitle(t) {
    if (t.artist) return { artist: t.artist, title: stripTrackNumber(t.title ?? '') }
    const raw = stripTrackNumber(t.title ?? '')
    const m = raw.match(/^(.+?)\s+[-–—]\s+(.+)$/)
    if (m) return { artist: m[1].trim(), title: m[2].trim() }
    return { artist: '', title: raw }
  }

  function addToQueue(track) {
    send({ type: 'queue_add', path: track.path, title: track.title,
           duration_sec: track.duration_sec, lufs: track.lufs,
           bpm: track.bpm, bitrate_kbps: track.bitrate_kbps })
  }

  function playNow(track) {
    skipNextCrossfade.set(true)
    send({ type: 'play_now', path: track.path, title: track.title,
           duration_sec: track.duration_sec, lufs: track.lufs,
           bpm: track.bpm, bitrate_kbps: track.bitrate_kbps })
  }

  function mixNow(track) {
    // Kein skipNextCrossfade → Player startet sofort Crossfade zum neuen Track
    send({ type: 'play_now', path: track.path, title: track.title,
           duration_sec: track.duration_sec, lufs: track.lufs,
           bpm: track.bpm, bitrate_kbps: track.bitrate_kbps })
  }

  function playNext(track) {
    send({ type: 'queue_insert_next', path: track.path, title: track.title,
           duration_sec: track.duration_sec })
  }

  // ── BetterVersion dialog ──────────────────────────────────────────────────
  let betterVersionTrack = $state(null)

  function openBetterVersion(track) {
    betterVersionTrack = track
    ctxMenu = null
  }

  // ── Context menu ─────────────────────────────────────────────────────────
  let ctxMenu = $state(null)  // { x, y, track }

  function onCtx(e, track) {
    e.preventDefault()
    e.stopPropagation()
    ctxMenu = { x: e.clientX, y: e.clientY, track }
  }
  function closeCtx() { ctxMenu = null }

  // ── Folder context menu (tree) ────────────────────────────────────────────
  let folderCtx    = $state(null)  // { x, y, path }
  let playlistCtx  = $state(null)  // { x, y, path, name }

  function onPlaylistCtx(e, pl) {
    e.preventDefault()
    e.stopPropagation()
    playlistCtx = { x: e.clientX, y: e.clientY, path: pl.path, name: pl.name }
  }

  // Ausgeschlossener Ordner, der diesen Pfad enthaelt (oder er selbst)
  function excludedBy(path) {
    const n = (p) => (p ?? '').replace(/\//g, '\\').replace(/\\$/, '').toLowerCase()
    const pn = n(path)
    return $excludedFolders.find(f => pn === n(f) || pn.startsWith(n(f) + '\\')) ?? null
  }
  function includeFolder(folder) {
    folderCtx = null
    send({ type: 'include_folder', folder })
  }

  // ── Statuszeile unten links: Menue ────────────────────────────────────────
  let libMenu = $state(null)   // {x, y}
  function toggleLibMenu(e) {
    e.stopPropagation()
    if (libMenu) { libMenu = null; return }
    const r = e.currentTarget.getBoundingClientRect()
    libMenu = { x: r.left, y: window.innerHeight - r.top + 4 }
  }

  // Kontextmenues ganz ins Fenster schieben — sie sind je nach Eintraegen
  // unterschiedlich hoch, eine feste Annahme schnitt das Ordner-Menue unten ab
  function fitMenu(node) {
    const r = node.getBoundingClientRect()
    if (r.bottom > window.innerHeight - 8) node.style.top = Math.max(8, window.innerHeight - r.height - 8) + 'px'
    if (r.right > window.innerWidth - 8) node.style.left = Math.max(8, window.innerWidth - r.width - 8) + 'px'
  }

  function onFolderCtx(e, path) {
    e.preventDefault()
    e.stopPropagation()
    folderCtx = { x: e.clientX, y: e.clientY, path }
  }

  // Titel der Bibliothek, die in diesem Ordner oder darunter liegen.
  // t.folder ist nur der Ordnername ("Drum and Bass"), nicht der Pfad — der
  // fruehere Vergleich damit fand nie etwas ("Keine Tracks gefunden").
  function tracksInFolder(fp, recursive = true) {
    const norm = (p) => (p ?? '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase()
    const fpN = norm(fp)
    return $library.filter(t => {
      const dir = norm(t.path).split('/').slice(0, -1).join('/')
      return dir === fpN || (recursive && dir.startsWith(fpN + '/'))
    })
  }

  function analyzeFolder(recursive) {
    if (!folderCtx) return
    const fp = folderCtx.path
    folderCtx = null
    const tracks = tracksInFolder(fp, recursive)
    if (tracks.length === 0) {
      alert(`Keine Tracks aus diesem Ordner in der Bibliothek gefunden.\nOrdner: ${fp}`)
      return
    }
    // Eine Anfrage fuer alle — das Backend arbeitet sie nacheinander ab
    send({ type: 'analyze_library_meta', paths: tracks.map(t => t.path) })
  }

  function scanFolderDupes() {
    if (!folderCtx) return
    const fp = folderCtx.path
    folderCtx = null
    dupeSourceMode = 'fs:' + fp
    navMode = 'duplicates'
    search = ''
    selected = new Set()
  }

  function excludeFolderFromLibrary() {
    if (!folderCtx) return
    const fp = folderCtx.path
    folderCtx = null
    const toRemove = tracksInFolder(fp)
    if (toRemove.length === 0) {
      alert(`Keine Tracks aus diesem Ordner in der Bibliothek gefunden.\nOrdner: ${fp}`)
      return
    }
    if (!confirm(`${toRemove.length} Titel aus der Bibliothek ausschließen? Die Dateien bleiben.\n\n` +
                 `Rückgängig: Rechtsklick auf den Ordner → „Wieder in die Bibliothek aufnehmen“.`)) return
    // Das Backend merkt sich den Ordner — einzeln entfernte Titel holte der
    // Ordner-Waechter frueher nach wenigen Sekunden zurueck.
    send({ type: 'exclude_folder', folder: fp })
  }

  // ── Delete confirm dialog ─────────────────────────────────────────────────
  let dlgDeletePaths = $state([])  // array of {path, title}
  function removeFromDisk(track) {
    dlgDeletePaths = [{ path: track.path, title: getTrackArtistTitle(track).title || track.path.split(/[\\/]/).pop() }]
    ctxMenu = null
  }
  function removeSelectedFromDisk() {
    const tracks = filtered.filter(t => selected.has(t.path))
    dlgDeletePaths = tracks.map(t => ({ path: t.path, title: getTrackArtistTitle(t).title || t.path.split(/[\\/]/).pop() }))
    ctxMenu = null
  }
  function confirmDeleteFromDisk() {
    trashPaths(dlgDeletePaths.map(d => d.path))
    selected = new Set()
    dlgDeletePaths = []
  }

  function qualityDot(track) {
    const ext = track.path?.split('.').pop()?.toLowerCase() ?? ''
    if ((track.cutoff_khz ?? 0) > 5 && track.cutoff_khz < CUTOFF_RED) return 'q-red'
    if ((track.cutoff_khz ?? 0) > 5 && track.cutoff_khz < CUTOFF_UPSCALED) return 'q-yellow'
    if (['flac', 'wav', 'aiff', 'aif', 'alac'].includes(ext)) return 'q-green'
    const br = track.bitrate_kbps ?? 0
    if (br >= 192) return 'q-green'
    if (br >= 128) return 'q-yellow'
    if (br > 0)    return 'q-red'
    return ''
  }

  function dragStart(e, track) {
    if (selected.has(track.path) && selected.size > 1) {
      // Drag all selected tracks
      const tracks = filtered.filter(t => selected.has(t.path))
      e.dataTransfer.setData('application/x-ytdl-multi', JSON.stringify(tracks))
      e.dataTransfer.setData('text/plain', tracks.map(t => t.path).join('\n'))
    } else {
      e.dataTransfer.setData('text/plain', track.path)
      e.dataTransfer.setData('application/x-ytdl-track', JSON.stringify({
        path: track.path, title: track.title, duration_sec: track.duration_sec,
        lufs: track.lufs, bpm: track.bpm, bitrate_kbps: track.bitrate_kbps
      }))
    }
    e.dataTransfer.effectAllowed = 'copy'
  }

  async function scanLibrary() {
    if (window.electron?.pickFolder) {
      const folder = await window.electron.pickFolder()
      if (folder) send({ type: 'scan_library', folder })
    } else {
      send({ type: 'scan_library', folder: '' })
    }
  }

  function handleRowClick(e, track) {
    selectionOwner.set('library')
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      const s = new Set(selected)
      if (s.has(track.path)) s.delete(track.path)
      else s.add(track.path)
      selected = s
    } else if (e.shiftKey && selected.size > 0) {
      const paths = filtered.map(t => t.path)
      const lastSel = [...selected].filter(p => paths.includes(p)).pop()
      const fromI = paths.indexOf(lastSel ?? '')
      const toI   = paths.indexOf(track.path)
      if (fromI >= 0 && toI >= 0) {
        const s = new Set(selected)
        const lo = Math.min(fromI, toI), hi = Math.max(fromI, toI)
        for (let i = lo; i <= hi; i++) s.add(paths[i])
        selected = s
      }
    } else {
      // Plain click: always set single selection (deselects all others)
      selected = new Set([track.path])
    }
  }

  // Selection is mutually exclusive between Library and Queue panels
  $effect(() => {
    if ($selectionOwner === 'queue' && selected.size > 0) selected = new Set()
  })

  function removeHiddenDupes() {
    if (!confirm(`${dupesHidden.size} Kopien in den Papierkorb?\n\nDie mit ✓ markierte Datei jedes Songs bleibt.`)) return
    trashPaths([...dupesHidden])
  }

  function clearPlayHistory() {
    if (!confirm('Wiedergabe-Verlauf löschen? Alle Abspielzähler werden auf 0 zurückgesetzt.')) return
    send({ type: 'clear_play_history' })
  }

  function selectAll() {
    selectionOwner.set('library')
    selected = new Set(filtered.map(t => t.path))
  }
  function clearSelection() { selected = new Set() }

  // Tracks whether the mouse is currently over the library panel — used to scope Ctrl+A
  let _libHover = $state(false)

  function addAllToQueue() {
    filtered.forEach(t => { if (!t._gone) addToQueue(t) })
  }

  // Dateien normalisieren: per Rechtsklick, Zielwert im Dialog waehlbar
  // (frueher fester Knopf in der Auswahlleiste mit dem Wiedergabe-Ziel)
  let normDlg = $state(null)     // { paths, lufs }
  function openNormalize(track) {
    const paths = selected.size > 1 && selected.has(track.path) ? [...selected] : [track.path]
    normDlg = { paths, lufs: $appSettings.targetLUFS ?? -10 }
    closeCtx()
  }
  function runNormalize() {
    send({ type: 'normalize_files', paths: normDlg.paths, target_lufs: normDlg.lufs, target_tp: -1.5 })
    normDlg = null
  }

  function removeSelectedFromLibrary() {
    // redirect: delete from disk (also removes from library)
    removeSelectedFromDisk()
  }

  function analyzeTrack(track) {
    send({ type: 'enrich_track', path: track.path, force: true })
    ctxMenu = null
  }

  // ── AcoustID fingerprinting ───────────────────────────────────────────────
  let identifyResult = $state(null)  // null | { title, artist, album, score, path, error }

  $effect(() => {
    if ($trackIdentified) {
      identifyResult = $trackIdentified
      trackIdentified.set(null)
    }
  })

  function identifyTrack(track) {
    identifyResult = null
    send({ type: 'identify_track', path: track.path })
    ctxMenu = null
  }

  function applyIdentifyResult() {
    if (!identifyResult || identifyResult.error) return
    send({ type: 'library_update_meta', path: identifyResult.path,
           title: identifyResult.title, artist: identifyResult.artist })
    identifyResult = null
  }

  function analyzeSelected() {
    for (const path of selected) send({ type: 'enrich_track', path, force: true })
    ctxMenu = null
  }

  function removeFromPlaylist(track) {
    const plPath = navMode.slice(9)
    send({ type: 'playlist_remove_track', playlist: plPath, path: track.path })
    ctxMenu = null
  }

  function removeSelectedFromPlaylist() {
    const plPath = navMode.slice(9)
    for (const path of selected) send({ type: 'playlist_remove_track', playlist: plPath, path })
    selected = new Set()
    ctxMenu = null
  }

  function openMetaEdit(track) {
    const at = getTrackArtistTitle(track)
    editTrack = { path: track.path, title: at.title, artist: at.artist }
    ctxMenu = null
  }

  function saveMetaEdit() {
    if (!editTrack) return
    send({ type: 'library_update_meta', path: editTrack.path,
           title: editTrack.title.trim(), artist: editTrack.artist.trim() })
    editTrack = null
  }

  function libKey(e) {
    if (e.target?.tagName === 'INPUT') return
    // Don't capture keystrokes that originate from the queue panel
    if (e.target?.closest?.('.queue')) return
    if (e.key === 'Escape') { clearSelection(); ctxMenu = null; editTrack = null }
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      if (!_libHover) return
      e.preventDefault()
      selectAll()
    }
    if (e.key === 'Delete' && selected.size > 0 && !editTrack) {
      e.preventDefault()
      if (navMode.startsWith('playlist:')) removeSelectedFromPlaylist()
      else if (navMode === 'duplicates' && dupeSourceMode.startsWith('playlist:')) {
        // scoped to playlist — remove from playlist, not disk
        const plPath = dupeSourceMode.slice(9)
        for (const path of selected) send({ type: 'playlist_remove_track', playlist: plPath, path })
        selected = new Set()
      }
      else removeSelectedFromLibrary()
    }
  }

  // ── Nav panel width splitter + collapse ──────────────────────────────────
  let navWidth        = $state(216)
  let navCollapsed    = $state(false)
  let _navWidthSaved  = 216

  function toggleNav() {
    if (navCollapsed) {
      navCollapsed = false
      navWidth     = _navWidthSaved
    } else {
      _navWidthSaved = navWidth
      navCollapsed   = true
    }
  }

  function startNavResize(e) {
    if (navCollapsed) return
    const startX = e.clientX
    const startW = navWidth
    function onMove(me) {
      navWidth = Math.max(140, Math.min(340, startW + me.clientX - startX))
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }
</script>

<div class="library" onmouseenter={() => _libHover = true} onmouseleave={() => _libHover = false}>

  <!-- ── Left nav panel ─────────────────────────────────────────────────── -->
  {#if !navCollapsed}
  <div class="nav" style="width: {navWidth}px">
    <!-- Top: Alle / Verlauf / Duplikate — always visible (static) -->
    <div class="nav-top">
      <button class="t-item {navMode === 'all' ? 'active' : ''}" onclick={() => selectNav('all')}>
        <i class="ti ti-music t-ico" aria-hidden="true"></i>
        <span class="t-name">Alle Titel</span>
        <span class="t-count">{$library.length - linkHidden.size - (hideDupesAuto ? globalDupes.hidden.size : 0)}</span>
      </button>
      <button class="t-item {navMode === 'recent' ? 'active' : ''}" onclick={() => selectNav('recent')}>
        <i class="ti ti-clock t-ico" aria-hidden="true"></i>
        <span class="t-name">Zuletzt gespielt</span>
      </button>
      <button class="t-item {navMode === 'downloads' ? 'active' : ''}"
              title="Alles, was heruntergeladen wurde, neueste zuerst"
              onclick={() => { if (!$downloadTreeLoaded) loadDlTree(); selectNav('downloads') }}>
        <i class="ti ti-clock-down t-ico" aria-hidden="true"></i>
        <span class="t-name">Downloads</span>
      </button>
      {#if globalDupes.groups.length > 0 || navMode === 'duplicates'}
        <button class="t-item {navMode === 'duplicates' ? 'active' : ''}" onclick={() => selectNav('duplicates')}
                title="{globalDupes.groups.length} Songs liegen mehrfach vor ({globalDupes.all.size} Dateien)">
          <i class="ti ti-copy t-ico t-ico-warn" aria-hidden="true"></i>
          <span class="t-name">Duplikate</span>
          <span class="t-count">{globalDupes.groups.length}</span>
        </button>
      {/if}
      {#if qualityInfo.map.size > 0 || navMode === 'quality'}
        <button class="t-item {navMode === 'quality' ? 'active' : ''}" onclick={() => selectNav('quality')}
                title="Titel mit schlechter Qualität: Musikvideo-Versionen, niedrige Bitrate, hochgerechnete Dateien">
          <i class="ti ti-alert-triangle t-ico t-ico-warn" aria-hidden="true"></i>
          <span class="t-name">Qualität</span>
          <span class="t-count">{qualityInfo.map.size}</span>
        </button>
      {/if}
      <div class="nav-sep nav-sep-top"></div>
    </div>

    <!-- Nav search -->
    <div class="nav-search-wrap">
      <i class="ti ti-search nav-search-ico" aria-hidden="true"></i>
      <input class="nav-search-input" type="text" placeholder="Navigation filtern…"
             bind:value={navSearch} aria-label="Navigation filtern" />
      {#if navSearch}
        <button class="btn btn-icon btn-sm" onclick={() => navSearch = ''} title="Filter löschen" aria-label="Filter löschen"><i class="ti ti-x"></i></button>
      {/if}
    </div>

    <!-- Scrollable tree section -->
    <div class="nav-tree">

    {#snippet dirNode(node, depth)}
      {@const k = _dk(node.path)}
      {@const open = dirOpen.has(k) || !!navQ}
      {@const kids = hasKids(node.path)}
      <div class="t-child t-dir {navMode === 'dir:' + node.path ? 'active' : ''}"
           role="treeitem" tabindex="0" aria-expanded={kids ? open : undefined} aria-selected={navMode === 'dir:' + node.path}
           style="padding-left:{6 + depth * 14}px"
           draggable="true" ondragstart={(e) => dirDragStart(e, node.path)}
           onclick={() => selectDir(node.path)}
           ondblclick={(e) => kids && toggleDir(node.path, e)}
           onkeydown={(e) => { if (e.key === 'Enter') selectDir(node.path); else if (e.key === 'ArrowRight' && kids && !open) toggleDir(node.path, e); else if (e.key === 'ArrowLeft' && open) toggleDir(node.path, e) }}
           oncontextmenu={(e) => onFolderCtx(e, node.path)}
           class:excluded={excludedBy(node.path)}
           title={node.path}>
        {#if kids}
          <span class="t-tog" role="button" tabindex="-1" aria-label={open ? 'Zuklappen' : 'Aufklappen'}
                onclick={(e) => toggleDir(node.path, e)}><i class="ti ti-chevron-right t-chevron" class:open aria-hidden="true"></i></span>
        {:else}
          <span class="t-tog"></span>
        {/if}
        <i class="ti {node.icon ?? 'ti-folder'} t-ico-sm" aria-hidden="true"></i>
        <span class="t-name">{node.name}</span>
        {#if node.count ?? dirCount(node.path)}<span class="t-count">{node.count ?? dirCount(node.path)}</span>{/if}
        {#if node.remove}
          <span class="row-act" role="button" tabindex="0" title="Aus dem Schnellzugriff entfernen" aria-label="Aus dem Schnellzugriff entfernen"
                onclick={(e) => { e.stopPropagation(); node.remove() }}
                onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), node.remove())}><i class="ti ti-x"></i></span>
        {/if}
      </div>
      {#if kids && dirOpen.has(k) && !navQ}
        {#each dirKids(node.path) as sub (sub.path)}
          {@render dirNode(sub, depth + 1)}
        {/each}
      {/if}
    {/snippet}

    <!-- 1. SCHNELLZUGRIFF (Favoriten-Ordner, aufklappbar) -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('quick')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('quick')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secQuickOpen} aria-hidden="true"></i>
      <i class="ti ti-star t-sec-ico" aria-hidden="true"></i>
      <span class="t-sec-label">Schnellzugriff</span>
      <span class="row-act" title="Ordner zum Schnellzugriff hinzufügen" aria-label="Ordner hinzufügen" role="button" tabindex="0"
            onclick={addQuickFolder}
            onkeydown={(e) => e.key === 'Enter' && addQuickFolder(e)}><i class="ti ti-plus"></i></span>
    </div>
    {#if secQuickOpen}
      {#if !$favorites.length}
        <div class="t-empty">Rechtsklick auf einen Ordner → „Zum Schnellzugriff“, oder +</div>
      {:else}
        {#each $favorites.filter(f => !navQ || f.name.toLowerCase().includes(navQ)) as fav (fav.path)}
          {@render dirNode({ path: fav.path, name: fav.name, remove: () => send({ type: 'remove_favorite', path: fav.path }) }, 0)}
        {/each}
      {/if}
    {/if}

    <!-- 2. MUSIKORDNER -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('dirs')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('dirs')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secDirsOpen} aria-hidden="true"></i>
      <i class="ti ti-folder t-sec-ico" aria-hidden="true"></i>
      <span class="t-sec-label">Musikordner</span>
    </div>
    {#if secDirsOpen}
      {#if navQ}
        {#each matchingDirs as d (d.path)}
          {@render dirNode(d, 0)}
        {:else}
          <div class="t-empty">Kein Ordner passt</div>
        {/each}
      {:else if !musicRoots.length}
        <div class="t-empty">Noch kein Musikordner · Einstellungen → System</div>
      {:else}
        {#each musicRoots as root (root)}
          {@render dirNode({ path: root, name: _base(root) }, 0)}
        {/each}
      {/if}
    {/if}

    <!-- 3. PLAYLISTEN -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('playlist')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('playlist')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secPlaylistOpen} aria-hidden="true"></i>
      <i class="ti ti-list t-sec-ico" aria-hidden="true"></i>
      <span class="t-sec-label">Playlisten</span>
      <span class="t-count">{zahl(filteredPlaylists.length, $playlists.length)}</span>
      <span class="row-act" title="Queue als Playlist speichern" aria-label="Queue als Playlist speichern" role="button" tabindex="0"
            onclick={(e) => { e.stopPropagation(); saveQueueAsPlaylist() }}
            onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), saveQueueAsPlaylist())}><i class="ti ti-plus"></i></span>
    </div>
    {#if secPlaylistOpen}
      {#if $playlists.length === 0}
        <div class="t-empty">Noch keine · + zum Speichern</div>
      {:else if filteredPlaylists.length === 0}
        <div class="t-empty">Keine Treffer</div>
      {:else}
        {#each filteredPlaylists as pl}
          <div class="t-child t-pl-row {navMode === 'playlist:' + pl.path ? 'active' : ''}
                      {dragOverPlaylist === pl.path ? 'pl-drop-hover' : ''}"
               role="button" tabindex="0"
               onclick={() => openPlaylistInLibrary(pl)}
               oncontextmenu={(e) => onPlaylistCtx(e, pl)}
               ondragover={(e) => { e.preventDefault(); dragOverPlaylist = pl.path }}
               ondragleave={() => dragOverPlaylist = null}
               ondrop={(e) => dropOnPlaylist(e, pl.path)}
               title={pl.name}>
            <i class="ti ti-list t-ico-sm" aria-hidden="true"></i>
            <span class="t-name">{pl.name}</span>
            {#if $playlistContent[pl.path]}
              <span class="t-count">{$playlistContent[pl.path].length}</span>
            {:else if pl.track_count > 0}
              <span class="t-count">{pl.track_count}</span>
            {/if}
            <span class="row-act" role="button" tabindex="0" title="In Queue laden" aria-label="In Queue laden"
                  onclick={(e) => { e.stopPropagation(); loadPlaylistToQueue(pl.path) }}
                  onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), loadPlaylistToQueue(pl.path))}><i class="ti ti-playlist-add"></i></span>
            <span class="row-act row-act-danger" role="button" tabindex="0" title="Playlist löschen" aria-label="Playlist löschen"
                  onclick={(e) => { e.stopPropagation(); deletePlaylist(pl.path) }}
                  onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), deletePlaylist(pl.path))}><i class="ti ti-trash"></i></span>
          </div>
        {/each}
      {/if}
    {/if}



    <!-- 4. COMPUTER (Laufwerke) -->
    <button class="t-sec-hdr" onclick={() => toggleSection('fs')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secFsOpen} aria-hidden="true"></i>
      <i class="ti ti-device-desktop t-sec-ico" aria-hidden="true"></i>
      <span class="t-sec-label">Computer</span>
    </button>
    {#if secFsOpen && fsReady}
      {#each fsRoots as drive (drive.path)}
        {@render dirNode({ path: drive.path, name: drive.name.replace(/\\$/, ''), icon: 'ti-device-desktop' }, 0)}
      {/each}
    {/if}

    <!-- 5. FILTER (ganz unten) -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('filter')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('filter')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secFilterOpen || !!navQ} aria-hidden="true"></i>
      <i class="ti ti-tags t-sec-ico" aria-hidden="true"></i>
      <span class="t-sec-label">Filter</span>
    </div>
    {#if secFilterOpen || navQ}
    <!-- 2. KÜNSTLER -->
    <button class="t-sec-hdr t-sub-hdr" onclick={() => toggleSection('artist')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secArtistOpen} aria-hidden="true"></i>
      <span class="t-sec-label">Künstler</span>
      <span class="t-count">{zahl(filteredArtists.length, artists.length)}</span>
    </button>
    {#if secArtistOpen}
      {#if filteredArtists.length > 8}
        <div class="artist-az">
          {#each AZ_LETTERS as letter}
            {@const has = filteredArtists.some(a => a.toUpperCase().startsWith(letter))}
            <button class="az-btn" disabled={!has}
                    onclick={() => has && jumpLetter('artist', letter)}
                    title={letter}>{letter}</button>
          {/each}
        </div>
      {/if}
      {#each filteredArtists as artist}
        <button class="t-child {navMode === 'artist:' + artist.toLowerCase() ? 'active' : ''}"
                onclick={() => selectNav('artist:' + artist.toLowerCase())}
                data-az={'artist:' + (artist[0]?.toLowerCase() ?? '#')}
                title={artist}>
          <i class="ti ti-user t-ico-sm" aria-hidden="true"></i>
          <span class="t-name">{artist}</span>
        </button>
      {/each}
    {/if}

    <!-- 3. ALBEN -->
    {#if albums.length > 0}
    <button class="t-sec-hdr t-sub-hdr" onclick={() => toggleSection('album')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secAlbumOpen} aria-hidden="true"></i>
      <span class="t-sec-label">Alben</span>
      <span class="t-count">{zahl(filteredAlbums.length, albums.length)}</span>
    </button>
    {#if secAlbumOpen}
      {#if filteredAlbums.length > 8}
        <div class="artist-az">
          {#each AZ_LETTERS as letter}
            {@const has = filteredAlbums.some(a => a.toUpperCase().startsWith(letter))}
            <button class="az-btn" disabled={!has}
                    onclick={() => has && jumpLetter('album', letter)}
                    title={letter}>{letter}</button>
          {/each}
        </div>
      {/if}
      {#each filteredAlbums as album}
        <button class="t-child {navMode === 'album:' + album.toLowerCase() ? 'active' : ''}"
                onclick={() => selectNav('album:' + album.toLowerCase())}
                data-az={'album:' + (album[0]?.toLowerCase() ?? '#')}
                title={album}>
          <i class="ti ti-vinyl t-ico-sm" aria-hidden="true"></i>
          <span class="t-name">{album}</span>
          <span class="t-count">{albumCounts.get(album.toLowerCase()) ?? 0}</span>
        </button>
      {/each}
    {/if}
    {/if}

    <!-- 4. GENRES -->
    {#if genres.length > 0 || !navQ}
    <div class="t-sec-hdr t-sub-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('genre')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('genre')}>
      <i class="ti ti-chevron-right t-chevron" class:open={secGenreOpen} aria-hidden="true"></i>
      <span class="t-sec-label">Genres</span>
      <span class="t-count">{zahl(filteredGenres.length, genres.length)}</span>
      <span class="row-act" title="Genres vereinheitlichen und fehlende ergänzen" aria-label="Genres ergänzen" role="button" tabindex="0"
            onclick={(e) => { e.stopPropagation(); showGenres = true }}
            onkeydown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); showGenres = true } }}><i class="ti ti-wand"></i></span>
    </div>
    {#if secGenreOpen}
      {#each filteredGenres as genre}
        <button class="t-child {navMode === 'genre:' + genre.toLowerCase() ? 'active' : ''}"
                onclick={() => selectNav('genre:' + genre.toLowerCase())}
                title={genre}>
          <i class="ti ti-tags t-ico-sm" aria-hidden="true"></i>
          <span class="t-name">{genre}</span>
          <span class="t-count">{genreCounts.get(genre.toLowerCase()) ?? 0}</span>
        </button>
      {/each}
    {/if}
    {/if}

    {/if}

    </div><!-- /nav-tree -->

    <!-- ── Scan footer — always visible ──────────────────────────────────── -->
    <div class="nav-footer">
      <div class="nav-sep nav-sep-footer"></div>
      <!-- Statuszeile: sonst ruhig, zeigt laufende Arbeit mit Fortschritt -->
      <div class="nav-status" role="status">
        <div class="ns-main">
          {#if $analyzeProgress}
            {@const { done, total } = $analyzeProgress}
            <span class="ns-text"><i class="ti ti-wand"></i> Analyse {done} / {total}</span>
            <div class="ap-bar"><div class="ap-fill" style="width:{total ? Math.round(done / total * 100) : 0}%"></div></div>
          {:else if $qualityScan}
            <span class="ns-text"><i class="ti ti-refresh spinner"></i> Höhen-Messung {$qualityScan.done} / {$qualityScan.total}</span>
            <div class="ap-bar"><div class="ap-fill" style="width:{$qualityScan.total ? Math.round($qualityScan.done / $qualityScan.total * 100) : 0}%"></div></div>
          {:else if $scanStatus}
            <span class="ns-text" title={$scanStatus}>{$scanStatus}</span>
          {:else}
            <span class="ns-text ns-idle">{$library.length} Titel</span>
          {/if}
        </div>
        {#if $analyzeProgress}
          <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'cancel_analyze' })} title="Analyse abbrechen" aria-label="Analyse abbrechen"><i class="ti ti-player-stop"></i></button>
        {/if}
        <button class="btn btn-icon btn-sm" class:is-active={libMenu} onclick={toggleLibMenu}
                title="Bibliothek: einlesen, analysieren, aufräumen" aria-label="Bibliothek-Menü" aria-haspopup="menu"><i class="ti ti-dots"></i></button>
      </div>
    </div>

  </div>
  {/if}

  <!-- ── Nav resize handle + collapse button ───────────────────────────── -->
  <div class="nav-handle-wrap">
    <div class="nav-handle {navCollapsed ? 'collapsed' : ''}"
         onmousedown={startNavResize} role="separator"></div>
    <button class="nav-collapse-btn" onclick={toggleNav}
            onmousedown={(e) => e.stopPropagation()}
            title={navCollapsed ? 'Baum einblenden' : 'Baum ausblenden'}>
      <i class="ti {navCollapsed ? 'ti-chevron-right' : 'ti-chevron-left'}" aria-hidden="true"></i>
    </button>
  </div>

  <!-- ── Right: track list ──────────────────────────────────────────────── -->
  <div class="tracks">

    <div class="toolbar">
      <div class="search-wrap">
        <i class="ti ti-search" aria-hidden="true"></i>
        <input class="field search" type="search" placeholder="Titel, Künstler, Ordner oder Tonart…"
          bind:value={search} aria-label="Bibliothek durchsuchen" />
      </div>
      <span class="tb-count">{filtered.length} Titel</span>
      {#if navMode === 'all' && globalDupes.hidden.size > 0}
        <button class="btn btn-ghost btn-sm dupe-hint" onclick={() => hideDupesAuto = !hideDupesAuto}
                title={hideDupesAuto ? 'Schlechtere Kopien doppelter Songs sind ausgeblendet. Klicken zeigt sie.' : 'Klicken blendet die schlechteren Kopien wieder aus.'}>
          <i class="ti ti-copy"></i>
          {#if hideDupesAuto}{globalDupes.hidden.size} Kopien ausgeblendet · <u>zeigen</u>{:else}<u>{globalDupes.hidden.size} Kopien ausblenden</u>{/if}
        </button>
      {/if}
      <span class="tb-spacer"></span>
      {#if navMode.startsWith('playlist:')}
        {@const activePlPath = navMode.slice(9)}
        <button class="btn btn-sm" onclick={() => loadPlaylistToQueue(activePlPath)}
                title="Gesamte Playlist in Queue laden"><i class="ti ti-playlist-add"></i> In Queue</button>
      {/if}
      {#if navMode === 'recent' && filtered.length > 0}
        <button class="btn btn-sm btn-danger" onclick={clearPlayHistory}
                title="Wiedergabe-Verlauf löschen (alle Abspielzähler zurücksetzen)">
          <i class="ti ti-trash"></i> Verlauf löschen
        </button>
      {/if}
      {#if plDupesGroups.length > 0}
        <button class="btn btn-sm" onclick={() => showPlDupeScan = true}
                title="Duplikate in dieser Playlist prüfen">
          Playlist-Duplikate ({plDupesGroups.length})
        </button>
      {/if}
      {#if filtered.length > 0 && selected.size === 0}
        <button class="btn btn-icon btn-sm" onclick={addAllToQueue} title="Alle angezeigten Titel einreihen" aria-label="Alle angezeigten Titel einreihen"><i class="ti ti-playlist-add"></i></button>
      {/if}
    </div>
    {#if navMode === 'quality'}
      <div class="dupe-info quality-info" role="status">
        <p class="dupe-text">
          {#if qualityInfo.map.size}<b>{qualityInfo.map.size} Titel mit schlechter Qualität.</b>{:else}<b>Keine Titel mit schlechter Qualität.</b>{/if}
          Maus über eine Zeile: <b>bessere Version</b> suchen oder mit <b>passt</b> ausblenden.
          {#if $qualityScan}
            <span class="q-scan"><i class="ti ti-refresh spinner"></i> Höhen-Messung läuft: {$qualityScan.done} / {$qualityScan.total}</span>
          {/if}
        </p>
        <div class="btn-group" role="radiogroup" aria-label="Grund">
          <button class="btn btn-sm" class:is-active={qualityFilter === 'all'} role="radio" aria-checked={qualityFilter === 'all'}
                  onclick={() => qualityFilter = 'all'}>Alle <span class="q-n">{qualityInfo.map.size}</span></button>
          {#each Object.keys(QUALITY_LABELS) as k}
            <button class="btn btn-sm" class:is-active={qualityFilter === k} role="radio" aria-checked={qualityFilter === k}
                    title={QUALITY_TIPS[k]} onclick={() => qualityFilter = k}>{QUALITY_LABELS[k]} <span class="q-n">{qualityInfo.counts[k]}</span></button>
          {/each}
          {#if qualityInfo.ignored.size}
            <button class="btn btn-sm" class:is-active={qualityFilter === 'ignored'} role="radio" aria-checked={qualityFilter === 'ignored'}
                    title="Mit „passt“ ausgeblendet" onclick={() => qualityFilter = 'ignored'}><i class="ti ti-eye-off"></i> <span class="q-n">{qualityInfo.ignored.size}</span></button>
          {/if}
        </div>
        {#if qualityFilter !== 'ignored' && filtered.length}
          <button class="btn btn-sm btn-primary" onclick={() => startQualityBatch((selected.size ? filtered.filter(t => selected.has(t.path)) : filtered).map(t => t.path))}
                  title="Für jeden Titel automatisch die beste Version suchen, Liste prüfen, dann gesammelt ersetzen">
            <i class="ti ti-list-check"></i> {selected.size ? `${selected.size} markierte` : `Alle ${filtered.length}`} automatisch ersetzen…
          </button>
        {/if}
      </div>
    {/if}
    {#if navMode === 'duplicates'}
      <div class="dupe-info" role="status">
        {#if dupesGroups.length > 0}
          <p class="dupe-text">
            <b>{dupesGroups.length} Songs doppelt</b>{dupeScopeLabel} · {dupesAll.size} Dateien.
            <i class="ti ti-circle-check d-keep-inline" aria-hidden="true"></i>
            <span class="dupe-best" title="Automatisch die beste: verlustfrei vor hoher Bitrate; Dateien von YouTube-Konvertern zählen höchstens wie 160 kbps.">bleibt</span>,
            die anderen sind Kopien. Maus über eine Zeile: <b>behalten</b> oder <b>Papierkorb</b>.
          </p>
          <div class="dupe-acts">
            <button class="btn btn-sm btn-danger" onclick={removeHiddenDupes}
                    title="Alle Kopien in den Papierkorb, die markierte Datei jedes Songs bleibt"><i class="ti ti-trash"></i> {dupesHidden.size} Kopien löschen</button>
          </div>
        {:else}
          <p class="dupe-text">Keine doppelten Songs{dupeScopeLabel}.</p>
        {/if}
        {#if ($appSettings.dupeAllowFolders ?? []).length}
          <div class="allow-list">
            <span class="allow-lbl" title="Kopien in diesen Ordnern zählen nicht als Duplikat. Ändern: Rechtsklick auf einen Ordner.">Doppelte erlaubt in:</span>
            {#each $appSettings.dupeAllowFolders as f (f)}
              <span class="allow-chip" title={f}>
                <i class="ti ti-copy-check" aria-hidden="true"></i>{f.split(/[\\/]/).filter(Boolean).pop()}
                <button class="allow-x" aria-label="Wieder als Duplikat melden" title="Wieder als Duplikat melden"
                        onclick={() => setDupeAllowed(f, false)}><i class="ti ti-x"></i></button>
              </span>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
    {#if $normalizeProgress}
      <div class="norm-progress">
        Normalisierung: {$normalizeProgress.done}/{$normalizeProgress.total} — {$normalizeProgress.current}
      </div>
    {/if}

    {#if selected.size > 0}
      <div class="sel-bar">
        <span class="sel-count">{selected.size} ausgewählt</span>
        {#if navMode === 'quality'}
          <button class="btn btn-sm btn-primary" onclick={() => startQualityBatch([...selected])}><i class="ti ti-list-check"></i> Bessere Versionen suchen ({selected.size})</button>
        {/if}
      </div>
    {/if}

    <div class="col-header-wrap">
      <div class="col-header" bind:this={_colHeaderEl}>
        <div class="col-pad"></div>
        {#if navMode === 'quality'}<div class="q-col" aria-hidden="true"></div>{/if}
        {#if navMode === 'duplicates'}<div class="d-col" aria-hidden="true"></div>{/if}
        {#if navMode === 'downloads'}
          <button class="dl-col dl-hdr col-btn {!dlColSort ? 'sort-active' : ''}" onclick={() => dlColSort = false}
                  title="Nach Ladedatum, neueste zuerst">Geladen{#if !dlColSort}<i class="ti ti-chevron-down sort-ico" aria-hidden="true"></i>{/if}</button>
        {:else if navMode.startsWith('playlist:')}
          <button class="pl-col dl-hdr col-btn {!dlColSort ? 'sort-active' : ''}" onclick={() => dlColSort = false}
                  title="Reihenfolge der Playlist">#{#if !dlColSort}<i class="ti ti-chevron-up sort-ico" aria-hidden="true"></i>{/if}</button>
        {/if}
        {#each cols as col, ci}
          <button
            class="col-btn {ci === 0 ? 'col-first' : ''} {sortCol === col.key && sortShown ? 'sort-active' : ''}"
            style="width:{colWidths[col.key] + (col.key === 'title' ? titleExtra : 0)}px;flex-shrink:0;{ci === 0 ? 'text-align:left' : ''};position:relative"
            draggable="true"
            ondragstart={(e) => onColDragStart(e, col.key)}
            ondragover={(e) => onColDragOver(e, col.key)}
            ondrop={(e) => onColDrop(e, col.key)}
            onclick={() => setSort(col.key)}>
            <span>{col.label}</span>{#if sortCol === col.key && sortShown}<i class="ti {sortAsc ? 'ti-chevron-up' : 'ti-chevron-down'} sort-ico" aria-hidden="true"></i>{/if}
          </button>
          <div class="col-resize-handle"
            onmousedown={(e) => startColResize(e, col.key)}
            ondblclick={(e) => { e.stopPropagation(); colWidths[col.key] = COL_DEFAULTS[col.key]; saveColState() }}
            title="Doppelklick: Breite zurücksetzen"></div>
        {/each}
        <div style="width:22px;flex-shrink:0"></div>
      </div>
      <!-- Picker lives outside the overflow container so the menu isn't clipped -->
      <div class="col-picker-area">
        <button class="btn btn-icon btn-sm" class:is-active={colPickerOpen} onclick={(e) => { e.stopPropagation(); colPickerOpen = !colPickerOpen }} title="Spalten wählen" aria-label="Spalten wählen"><i class="ti ti-columns-3"></i></button>
        {#if colPickerOpen}
          <div class="col-picker-menu" role="menu">
            {#each ALL_COL_DEFS as c}
              {#if c.key !== 'title'}
                <label class="col-picker-item" onclick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={colVisible[c.key]} onchange={() => { colVisible[c.key] = !colVisible[c.key]; saveColState() }}>
                  {c.label}
                </label>
              {/if}
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <div class="rows" bind:this={_rowsEl} onscroll={onRowsScroll}>
      {#if filtered.length === 0}
        <div class="empty">
          {navMode.startsWith('playlist:') && !$playlistContent[navMode.slice(9)]
            ? 'Lade Playlist…'
            : $library.length === 0
              ? 'Noch keine Tracks · Ordner scannen'
              : 'Keine Treffer'}
        </div>
        {#if search.length > 2 && $library.length > 0}
          <div class="yt-fallback">
            <span class="yt-hint">Nicht in Bibliothek —</span>
            <button class="btn btn-primary btn-sm" onclick={() => send({ type: 'search', query: search })}><i class="ti ti-download"></i> Auf YouTube suchen</button>
          </div>
        {/if}
      {:else}
        <div style="height:{filtered.length * ROW_H}px; position:relative; min-width:max-content">
          <div style="position:absolute; top:{_vStart * ROW_H}px; width:100%">
            {#each _vItems as track, vi (track.path)}
              {@const qdot = qualityDot(track)}
              {@const at = getTrackArtistTitle(track)}
              <div class="row {(track.play_count ?? 0) > 0 ? 'played' : ''} {selected.has(track.path) ? 'sel' : ''} {track.missing ? 'missing' : ''} {navMode === 'duplicates' && dupesHidden.has(track.path) ? 'dupe-copy' : ''} {dupeGrpStart.has(track.path) ? 'grp-start' : ''}"
                   role="row"
                   draggable={!track._gone}
                   ondragstart={(e) => dragStart(e, track)}
                   onclick={(e) => handleRowClick(e, track)}
                   ondblclick={(e) => track._gone ? null : navMode === 'quality' ? openBetterVersion(track) : onCtx(e, track)}
                   oncontextmenu={(e) => track._gone ? e.preventDefault() : onCtx(e, track)}>
                <div class="col-pad">
                  {#if qdot}<span class="qdot {qdot}"></span>{/if}
                </div>
                {#if navMode === 'quality'}
                  {@const reasons = qualityInfo.map.get(track.path) ?? qualityInfo.ignored.get(track.path) ?? []}
                  <span class="q-col">
                    <span class="q-icon" role="img" aria-label={reasons.map(k => QUALITY_LABELS[k]).join(', ')}
                          onmouseenter={(e) => showQTip(e, track)} onmouseleave={() => qTip = null}>
                      {#if reasons.length}
                        <i class="ti ti-alert-triangle q-warn {(track.cutoff_khz ?? 0) > 5 && track.cutoff_khz < CUTOFF_RED ? 'q-bad' : ''} {track.quality_ok ? 'q-off' : ''}" aria-hidden="true"></i>
                      {/if}
                    </span>
                    <button class="r-btn" title="Bessere Version suchen" aria-label="Bessere Version suchen"
                            onclick={(e) => { e.stopPropagation(); openBetterVersion(track) }}><i class="ti ti-refresh"></i></button>
                    {#if track.quality_ok}
                      <button class="r-btn" title="Wieder anzeigen" aria-label="Wieder anzeigen"
                              onclick={(e) => { e.stopPropagation(); qualityOk(track, false) }}><i class="ti ti-eye"></i></button>
                    {:else}
                      <button class="r-btn" title="Passt so — aus der Liste ausblenden" aria-label="Passt so"
                              onclick={(e) => { e.stopPropagation(); qualityOk(track, true) }}><i class="ti ti-eye-off"></i></button>
                    {/if}
                  </span>
                {/if}
                {#if navMode === 'duplicates'}
                  {@const g = dupeGroupOf.get(track.path)}
                  <span class="d-col">
                    {#if g?.best.path === track.path}
                      <i class="ti ti-circle-check d-keep" title="Diese Datei bleibt" aria-label="Bleibt"></i>
                    {:else}
                      <button class="r-btn" title="Diese Datei behalten statt der markierten" aria-label="Behalten"
                              onclick={(e) => { e.stopPropagation(); keepDupe(track) }}><i class="ti ti-circle-check"></i></button>
                    {/if}
                    <button class="r-btn r-danger" title="Diese Datei in den Papierkorb" aria-label="In den Papierkorb"
                            onclick={(e) => { e.stopPropagation(); trashOne(track) }}><i class="ti ti-trash"></i></button>
                  </span>
                {/if}
                {#if navMode.startsWith('playlist:')}
                  <span class="pl-col">{playlistPos.get(track.path) ?? ''}</span>
                {/if}
                {#if navMode === 'downloads'}
                  <span class="dl-col" title={track._gone ? 'Datei wurde gelöscht' : ''}>
                    <span class="dl-date">{fmtMtime(track._dlTs)}</span>
                    {#if track._gone && track._url}
                      <button class="r-btn" title="Neu herunterladen" aria-label="Neu herunterladen"
                              onclick={(e) => { e.stopPropagation(); send({ type: 'download_add', url: track._url, format: 'mp3-best' }) }}><i class="ti ti-download"></i></button>
                    {/if}
                  </span>
                {/if}
                {#each cols as col}
                  {#if col.key === 'title'}
                    <span class="cell c-title" style="width:{colWidths.title + titleExtra}px" title={track.title}>
                      <span class="c-title-text">{at.title}</span>
                      {#if (track.play_count ?? 0) > 0}<span class="pc-badge">×{track.play_count}</span>{/if}
                    </span>
                  {:else if col.key === 'artist'}
                    <span class="cell c-artist" style="width:{colWidths.artist}px" title={at.artist}>{at.artist}</span>
                  {:else if col.key === 'album'}
                    <span class="cell c-folder" style="width:{colWidths.album}px" title={track.album || ''}>{track.album || ''}</span>
                  {:else if col.key === 'genre'}
                    <span class="cell c-folder" style="width:{colWidths.genre}px" title={track.genre || ''}>{track.genre || ''}</span>
                  {:else if col.key === 'album_artist'}
                    <span class="cell c-folder" style="width:{colWidths.album_artist}px" title={track.album_artist || ''}>{track.album_artist || ''}</span>
                  {:else if col.key === 'folder'}
                    <span class="cell c-folder" style="width:{colWidths.folder}px" title={track.folder || ''}>{track.folder || ''}</span>
                  {:else if col.key === 'ext'}
                    <span class="cell num c-ext" style="width:{colWidths.ext}px">{track.ext || ''}</span>
                  {:else if col.key === 'duration'}
                    <span class="cell num" style="width:{colWidths.duration}px">{fmt(track.duration_sec)}</span>
                  {:else if col.key === 'lufs'}
                    <span class="cell num" style="width:{colWidths.lufs}px">
                      {#if track.unanalyzable}
                        <span class="lufs-err" title="Datei konnte nicht analysiert werden (korrupt/unlesbar)">nicht lesbar</span>
                      {:else if (track.lufs ?? -99) > -90}
                        {track.lufs?.toFixed(1)}
                      {/if}
                    </span>
                  {:else if col.key === 'bpm'}
                    <span class="cell num" style="width:{colWidths.bpm}px">{track.bpm || ''}</span>
                  {:else if col.key === 'key'}
                    <span class="cell c-key" style="width:{colWidths.key}px"><KeyChip key={track.key} src={track.key_src} /></span>
                  {:else if col.key === 'bitrate'}
                    <span class="cell num" style="width:{colWidths.bitrate}px">{track.bitrate_kbps || ''}</span>
                  {:else if col.key === 'comment'}
                    <span class="cell c-comment" style="width:{colWidths.comment}px" title={track.comment || ''}>{track.comment || ''}</span>
                  {:else if col.key === 'energy'}
                    <span class="cell num" style="width:{colWidths.energy}px" title={track.energy ? 'Energie-Level von Mixed In Key' : ''}>{track.energy || ''}</span>
                  {:else if col.key === 'mtime'}
                    <span class="cell num" style="width:{colWidths.mtime}px">{fmtMtime(track.mtime)}</span>
                  {/if}
                  <div class="col-resize-handle" style="flex-shrink:0;pointer-events:none;cursor:default"></div>
                {/each}
                <div class="actions">
                  {#if (track.bitrate_kbps > 0 && track.bitrate_kbps < 128) || ((track.lufs ?? -99) > -90 && track.lufs < -20)}
                    <button onclick={(e) => { e.stopPropagation(); send({ type: 'search', query: track.title }) }} title="Schlechte Qualität – erneut herunterladen" aria-label="Schlechte Qualität – erneut herunterladen" class="btn btn-icon btn-sm row-warn"><i class="ti ti-alert-triangle"></i></button>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>

  </div>

</div>

<!-- Click-outside / right-click-outside to close context menu -->
<svelte:window onclick={() => { closeCtx(); folderCtx = null; playlistCtx = null; colPickerOpen = false; favOpen = false; libMenu = null }} oncontextmenu={() => { if (!ctxMenu) return; closeCtx() }} onkeydown={libKey} />

{#if qTip && qTip.reasons.length}
  <div class="q-tip" role="tooltip" style="left:{qTip.x}px;top:{qTip.y}px">
    {#each qTip.reasons as k}
      <span class="q-tip-row"><b>{QUALITY_LABELS[k]}</b> {QUALITY_TIPS[k]}{#if k === 'upscaled' && qTip.track.cutoff_khz} (Höhen bis {qTip.track.cutoff_khz} kHz){:else if k === 'bitrate'} ({qTip.track.bitrate_kbps} kbps){/if}</span>
    {/each}
    <span class="q-tip-hint">Doppelklick: bessere Version suchen</span>
  </div>
{/if}

{#if titlePaths !== undefined}
  <TitleDialog paths={titlePaths} onclose={() => titlePaths = undefined} />
{/if}
{#if fpPaths}
  <TitleDialog paths={fpPaths} mode="fingerprint" onclose={() => fpPaths = null} />
{/if}

{#if normDlg}
  <div class="dlg-overlay" onclick={() => normDlg = null} role="presentation">
    <div class="dlg" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Normalisieren" style="width:420px">
      <div class="dlg-title">{normDlg.paths.length === 1 ? 'Datei' : `${normDlg.paths.length} Dateien`} normalisieren</div>
      <div class="dlg-hint">Bringt die Lautheit dauerhaft auf den Zielwert — die Dateien werden dabei neu geschrieben. Für die Wiedergabe reicht meist die Lautstärke-Angleichung in den Einstellungen.</div>
      <label class="dlg-field">Ziel: <b>{normDlg.lufs} LUFS</b>
        <input type="range" min="-18" max="-5" step="1" bind:value={normDlg.lufs} />
      </label>
      <div class="dlg-actions">
        <button class="btn" onclick={() => normDlg = null}>Abbrechen</button>
        <button class="btn btn-primary" onclick={runNormalize}>Normalisieren</button>
      </div>
    </div>
  </div>
{/if}


{#if showGenres}
  <GenreDialog onclose={() => showGenres = false} />
{/if}

{#if betterVersionTrack}
  <BetterVersionDialog track={betterVersionTrack} onclose={() => betterVersionTrack = null} />
{/if}

{#if identifyResult}
  <div class="dlg-overlay" onclick={() => identifyResult = null} role="presentation">
    <div class="dlg" onclick={(e) => e.stopPropagation()} role="dialog">
      <div class="dlg-title">Fingerprint-Erkennung (AcoustID)</div>
      {#if identifyResult.error}
        <div class="notice error">{identifyResult.error}</div>
      {:else}
        <div class="id-score">
          Match-Score: {Math.round((identifyResult.score ?? 0) * 100)}%
          {#if identifyResult._source}
            <span class="id-source">via {identifyResult._source}</span>
          {/if}
        </div>
        <div class="id-field"><span class="id-lbl">Titel</span><span class="id-val">{identifyResult.title || '—'}</span></div>
        <div class="id-field"><span class="id-lbl">Künstler</span><span class="id-val">{identifyResult.artist || '—'}</span></div>
        <div class="id-field"><span class="id-lbl">Album</span><span class="id-val">{identifyResult.album || '—'}</span></div>
      {/if}
      <div class="dlg-actions">
        <button class="btn" onclick={() => identifyResult = null}>Schließen</button>
        {#if identifyResult.fix_tab}
          <button class="btn btn-primary" onclick={() => { const t = identifyResult.fix_tab; identifyResult = null; openSettings(t) }}>
            In den Einstellungen einrichten
          </button>
        {:else if !identifyResult.error}
          <button class="btn btn-primary" onclick={applyIdentifyResult}>Metadaten übernehmen</button>
        {/if}
      </div>
    </div>
  </div>
{/if}

{#if showDupeScan}
  <DuplicateScanDialog groups={dupesGroups} onclose={() => showDupeScan = false}
    onremove={(path) => trashPaths([path])} />
{/if}

{#if showPlDupeScan}
  {@const plPath = navMode.startsWith('playlist:') ? navMode.slice(9) : (playlistCtx?.path ?? '')}
  <DuplicateScanDialog groups={plDupesGroups} playlistMode={true} onclose={() => showPlDupeScan = false}
    onremove={(path) => send({ type: 'playlist_remove_track', playlist: plPath, path })} />
{/if}

{#if editTrack}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="dlg-overlay" onclick={() => editTrack = null} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Metadaten bearbeiten</div>
    <label class="dlg-field">Titel
      <input class="field" bind:value={editTrack.title}
             onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') saveMetaEdit(); if (e.key === 'Escape') editTrack = null }} />
    </label>
    <label class="dlg-field">Künstler
      <input class="field" bind:value={editTrack.artist}
             onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') saveMetaEdit(); if (e.key === 'Escape') editTrack = null }} />
    </label>
    <div class="dlg-hint">Speichert in der Datei (überschreibt ID3-Tags) · Enter zum Speichern</div>
    <div class="dlg-actions">
      <button class="btn" onclick={() => editTrack = null}>Abbrechen</button>
      <button class="btn btn-primary" onclick={saveMetaEdit}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if plNameDialog}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="dlg-overlay" onclick={() => plNameDialog = false} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Playlist speichern</div>
    <label class="dlg-field">Name
      <input class="field" bind:value={plNameValue} placeholder="Playlist-Name…"
             onkeydown={(e) => { if (e.key === 'Enter') confirmSavePlaylist(); if (e.key === 'Escape') plNameDialog = false }}
             use:focus />
    </label>
    <div class="dlg-actions">
      <button class="btn" onclick={() => plNameDialog = false}>Abbrechen</button>
      <button class="btn btn-primary" onclick={confirmSavePlaylist}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if plDeletePath}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="dlg-overlay" onclick={() => plDeletePath = null} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Playlist löschen?</div>
    <div class="dlg-hint">Die Playlist-Datei wandert in den Papierkorb. Die Titel selbst bleiben unberührt.</div>
    <div class="dlg-actions">
      <button class="btn" onclick={() => plDeletePath = null}>Abbrechen</button>
      <button class="btn btn-danger"
              onclick={() => { send({ type: 'delete_playlist', path: plDeletePath }); plDeletePath = null }}>In den Papierkorb</button>
    </div>
  </div>
</div>
{/if}

{#if dlgDeletePaths.length > 0}
  <div class="dlg-overlay" onclick={() => dlgDeletePaths = []}>
    <div class="dlg" onclick={(e) => e.stopPropagation()}>
      <div class="dlg-title">In den Papierkorb verschieben?</div>
      {#if dlgDeletePaths.length === 1}
        <div class="dlg-hint">„{dlgDeletePaths[0].title}" wird in den Papierkorb verschoben.</div>
      {:else}
        <div class="dlg-hint">{dlgDeletePaths.length} Tracks werden in den Papierkorb verschoben.</div>
        <div class="dlg-list">
          {#each dlgDeletePaths as t}
            <div>{t.title}</div>
          {/each}
        </div>
      {/if}
      <div class="dlg-actions">
        <button class="btn" onclick={() => dlgDeletePaths = []}>Abbrechen</button>
        <button class="btn btn-danger" onclick={confirmDeleteFromDisk}>In den Papierkorb</button>
      </div>
    </div>
  </div>
{/if}

{#if ctxMenu}
  <div class="ctx-menu" use:fitMenu style="left:{Math.min(ctxMenu.x, window.innerWidth - 210)}px;top:{Math.min(ctxMenu.y, window.innerHeight - 220)}px">
    <button onclick={() => { playNow(ctxMenu.track); ctxMenu = null }}>Jetzt abspielen</button>
    <button class="ctx-mix" onclick={() => { mixNow(ctxMenu.track); ctxMenu = null }}>Jetzt mischen</button>
    <button onclick={() => { playNext(ctxMenu.track); ctxMenu = null }}>Als Nächstes einfügen</button>
    <button onclick={() => { addToQueue(ctxMenu.track); ctxMenu = null }}>Zur Warteschlange</button>
    <div class="ctx-sep"></div>
    <button onclick={() => { window.electron?.openPath(ctxMenu.track.path); ctxMenu = null }}>Im Explorer zeigen</button>
    {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
      <button onclick={analyzeSelected}>{selected.size} Tracks analysieren (BPM · LUFS)</button>
    {:else if ctxMenu.track.unanalyzable}
      <button disabled title="Datei ist korrupt oder nicht lesbar">Track analysieren (BPM · LUFS)</button>
    {:else}
      <button onclick={() => analyzeTrack(ctxMenu.track)}>Track analysieren (BPM · LUFS)</button>
    {/if}
    {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
      <button onclick={() => { fpPaths = [...selected]; closeCtx() }}>{selected.size} Titel per Fingerprint erkennen…</button>
    {:else if $acoustidApiKey}
      <button onclick={() => identifyTrack(ctxMenu.track)}>Fingerprint-Erkennung (AcoustID)</button>
    {/if}
    {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
      <button onclick={() => { startQualityBatch([...selected]); closeCtx() }}>{selected.size} Titel: bessere Versionen suchen…</button>
    {:else}
      <button onclick={() => openBetterVersion(ctxMenu.track)}>Bessere Version suchen</button>
    {/if}
    <button onclick={() => openMetaEdit(ctxMenu.track)}>Metadaten bearbeiten</button>
    <button onclick={() => { titlePaths = selected.size > 1 && selected.has(ctxMenu.track.path) ? [...selected] : [ctxMenu.track.path]; closeCtx() }}>
      {selected.size > 1 && selected.has(ctxMenu.track.path) ? `${selected.size} Titel` : 'Titel'} aufräumen…</button>
    <button onclick={() => openNormalize(ctxMenu.track)}>{selected.size > 1 && selected.has(ctxMenu.track.path) ? `${selected.size} Dateien` : 'Datei'} normalisieren…</button>
    {#if navMode === 'duplicates'}
      {@const dir = ctxMenu.track.path.replace(/[\\/][^\\/]*$/, '')}
      <button onclick={() => setDupeAllowed(dir, true)} title={dir}>Doppelte im Ordner „{dir.split(/[\\/]/).filter(Boolean).pop()}“ erlauben</button>
    {/if}
    <div class="ctx-sep"></div>
    {#if navMode.startsWith('playlist:')}
      {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
        <button class="ctx-danger" onclick={removeSelectedFromPlaylist}>{selected.size} Tracks aus Playlist entfernen</button>
      {:else}
        <button class="ctx-danger" onclick={() => removeFromPlaylist(ctxMenu.track)}>Aus Playlist entfernen</button>
      {/if}
      <div class="ctx-sep"></div>
    {/if}
    {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
      <button class="ctx-danger" onclick={removeSelectedFromDisk}>{selected.size} Titel in den Papierkorb</button>
    {:else}
      <button class="ctx-danger" onclick={() => removeFromDisk(ctxMenu.track)}>In den Papierkorb</button>
    {/if}
  </div>
{/if}

{#if libMenu}
  <div class="ctx-menu lib-menu" role="menu" style="left:{libMenu.x}px;bottom:{libMenu.y}px" onclick={(e) => e.stopPropagation()}>
    <button onclick={() => { libMenu = null; scanLibrary() }}><i class="ti ti-scan"></i> Ordner neu einlesen</button>
    <button onclick={() => { libMenu = null; send({ type: 'analyze_library_meta' }) }}
            title="LUFS, BPM und Tonart für alle Titel, die noch keinen Wert haben"><i class="ti ti-wand"></i> Fehlende Werte analysieren</button>
    <div class="ctx-sep"></div>
    <button onclick={() => { libMenu = null; selectNav('quality') }}><i class="ti ti-alert-triangle"></i> Qualität prüfen</button>
    <button onclick={() => { libMenu = null; selectNav('duplicates') }}><i class="ti ti-copy"></i> Duplikate</button>
    <button onclick={() => { libMenu = null; showGenres = true }}><i class="ti ti-tags"></i> Genres ergänzen…</button>
    <button onclick={() => { libMenu = null; titlePaths = null }}><i class="ti ti-clear-formatting"></i> Titel aufräumen…</button>
  </div>
{/if}

{#if folderCtx}
  <div class="ctx-menu" use:fitMenu style="left:{Math.min(folderCtx.x, window.innerWidth - 220)}px;top:{Math.min(folderCtx.y, window.innerHeight - 160)}px"
       onclick={(e) => e.stopPropagation()}>
    <button onclick={() => analyzeFolder(false)}>Analysieren — nur dieser Ordner</button>
    <button onclick={() => analyzeFolder(true)}>Analysieren — mit Unterordnern</button>
    <button onclick={scanFolderDupes}>Auf Duplikate scannen</button>
    <button onclick={() => { const p = tracksInFolder(folderCtx.path, true).map(t => t.path); folderCtx = null; if (p.length) fpPaths = p }}
            title="Alle Titel in diesem Ordner und darunter am Klang erkennen (AcoustID) — Vorschläge zum Prüfen, nichts wird ungefragt geändert">Per Fingerprint erkennen…</button>
    <button onclick={() => { window.electron?.openPath(folderCtx.path); folderCtx = null }}>Im Explorer öffnen</button>
    <div class="ctx-sep"></div>
    {#if $favorites.some(f => f.path === folderCtx.path)}
      <button onclick={() => { send({ type: 'remove_favorite', path: folderCtx.path }); folderCtx = null }}>Aus dem Schnellzugriff entfernen</button>
    {:else}
      <button onclick={() => { send({ type: 'add_favorite', path: folderCtx.path, name: folderCtx.path.split(/[\\/]/).filter(Boolean).pop() ?? folderCtx.path }); folderCtx = null }}>Zum Schnellzugriff hinzufügen</button>
    {/if}
    <div class="ctx-sep"></div>
    {#if allowedFolderOf(folderCtx.path)}
      {@const af = allowedFolderOf(folderCtx.path)}
      <button onclick={() => setDupeAllowed(af, false)} title={af}>
        <i class="ti ti-copy" aria-hidden="true"></i>
        {_normDir(af) === _normDir(folderCtx.path) ? 'Doppelte hier wieder melden' : `Doppelte in „${af.split(/[\\/]/).filter(Boolean).pop()}“ wieder melden`}
      </button>
    {:else}
      <button onclick={() => setDupeAllowed(folderCtx.path, true)}
              title="Für Event- oder Playlist-Ordner: Jeder Unterordner gilt einzeln — derselbe Song in zwei Unterordnern ist okay, zweimal im selben wird gemeldet">
        <i class="ti ti-copy-check" aria-hidden="true"></i> Doppelte hier erlauben
      </button>
    {/if}
    {#if excludedBy(folderCtx.path)}
      {@const ex = excludedBy(folderCtx.path)}
      <button onclick={() => includeFolder(ex)} title={ex}>
        {ex.toLowerCase() === folderCtx.path.toLowerCase() ? 'Wieder in die Bibliothek aufnehmen' : `Ganzen Ordner „${ex.split(/[\\/]/).filter(Boolean).pop()}“ wieder aufnehmen`}
      </button>
    {:else}
      <button onclick={excludeFolderFromLibrary}>Aus Bibliothek ausschließen</button>
    {/if}
  </div>
{/if}

{#if playlistCtx}
  <div class="ctx-menu" use:fitMenu style="left:{Math.min(playlistCtx.x, window.innerWidth - 220)}px;top:{Math.min(playlistCtx.y, window.innerHeight - 120)}px"
       onclick={(e) => e.stopPropagation()}>
    <button onclick={() => {
      const pl = $playlists.find(p => p.path === playlistCtx.path)
      if (pl) openPlaylistInLibrary(pl)
      showPlDupeScan = true
      playlistCtx = null
    }}>Auf Duplikate scannen</button>
    <div class="ctx-sep"></div>
    <button onclick={() => { loadPlaylistToQueue(playlistCtx.path); playlistCtx = null }}>In Queue laden</button>
    <button class="ctx-danger" onclick={() => { deletePlaylist(playlistCtx.path); playlistCtx = null }}>Löschen</button>
  </div>
{/if}

<style>
  .library { display: flex; flex: 1; overflow: hidden; height: 100%; }

  /* ── Navigation links ──────────────────────────────────────────────────
     Ruhige Hierarchie: Abschnitte als kleine Grossbuchstaben-Etiketten,
     Eintraege in Standardschrift, der aktive Eintrag mit Flaeche und Kante.
     Zaehler rechtsbuendig, dezent, aber lesbar. */
  .nav {
    display: flex; flex-direction: column; flex-shrink: 0; overflow: hidden;
    background: var(--c-bg2); border-right: 1px solid var(--c-br1);
  }
  .nav-top { flex-shrink: 0; display: flex; flex-direction: column; padding: var(--sp-2) var(--sp-2) 0; gap: 1px; }
  .nav-tree {
    flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden;
    display: flex; flex-direction: column; padding: 0 var(--sp-2) var(--sp-2); gap: 1px;
  }
  .nav-sep { height: 1px; background: var(--c-br1); margin: var(--sp-2) var(--sp-1); flex-shrink: 0; }

  .t-item, .t-child {
    display: flex; align-items: center; gap: var(--sp-2);
    width: 100%; height: var(--nav-h); padding: 0 var(--sp-2);
    background: none; border: none; border-radius: var(--r-s);
    color: var(--c-tx2); font: var(--nav-fs) 'Segoe UI', system-ui, sans-serif; text-align: left;
    cursor: pointer; flex-shrink: 0; overflow: hidden; position: relative;
  }
  .t-item { font-weight: 600; }
  .t-item:hover, .t-child:hover { background: var(--c-hover); color: var(--c-tx1); }
  .t-item.active, .t-child.active { background: var(--c-act-bg); color: var(--c-accent-tx); }
  .t-item.active::before, .t-child.active::before {
    content: ""; position: absolute; left: 0; top: 6px; bottom: 6px; width: 3px;
    border-radius: 2px; background: var(--c-accent);
  }
  .t-child { padding-left: 22px; }
  .t-d2 { padding-left: 34px; }
  .t-d3 { padding-left: 46px; }
  .t-d4 { padding-left: 58px; }
  .t-d5 { padding-left: 70px; }

  .t-ico, .t-ico-sm { font-size: 15px; flex-shrink: 0; color: var(--c-tx4); }
  .t-ico-sm { font-size: 14px; }
  .t-ico-warn { color: var(--c-warn-tx); }
  .t-item.active .t-ico, .t-child.active .t-ico-sm { color: var(--c-accent-tx); }

  .t-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .t-count {
    flex-shrink: 0; font-size: var(--fs-cap); color: var(--c-tx5);
    font-variant-numeric: tabular-nums; font-weight: 400;
    letter-spacing: 0; text-transform: none;
  }
  .t-item.active .t-count, .t-child.active .t-count { color: var(--c-accent-tx); }

  /* Abschnittskopf */
  .t-sec-hdr {
    display: flex; align-items: center; gap: 6px;
    width: 100%; height: 30px; padding: 0 var(--sp-2) 0 var(--sp-1); margin-top: var(--sp-2);
    background: none; border: none; border-radius: var(--r-s);
    color: var(--c-tx4); font: 700 var(--fs-cap) 'Segoe UI', system-ui, sans-serif;
    letter-spacing: .08em; text-transform: uppercase; text-align: left;
    cursor: pointer; flex-shrink: 0;
  }
  .t-sec-hdr:hover { color: var(--c-tx1); }
  .t-sec-label { flex: 1; }
  .t-chevron { font-size: 13px; flex-shrink: 0; transition: transform .15s; }
  .t-sec-ico { font-size: 14px; opacity: .75; flex-shrink: 0; }
  .t-sub-hdr { padding-left: 22px; text-transform: none; letter-spacing: 0; font-size: var(--fs-body); font-weight: 600; }
  .t-dir { cursor: pointer; }
  .t-tog { width: 16px; height: 16px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
           color: var(--c-tx4); border-radius: var(--r-s); }
  .t-tog:hover { color: var(--c-tx1); background: var(--c-hover); }
  .t-chevron.open { transform: rotate(90deg); }
  .t-pinned-hdr { cursor: default; }
  .t-pinned-hdr:hover { color: var(--c-tx4); }

  .t-toggle-ico {
    width: 14px; flex-shrink: 0; text-align: center;
    font-size: var(--fs-body); font-weight: 700; line-height: 1; color: var(--c-tx4);
  }
  .dl-tog { cursor: pointer; border-radius: var(--r-s); }
  .dl-tog:hover { color: var(--c-accent-tx); background: var(--c-bg5); }

  /* Kleine Aktion am Ende einer Zeile (Anheften, Loeschen, Neu laden).
     Unsichtbar bis zum Hover, per Tastatur erreichbar. */
  .row-act {
    display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
    width: 24px; height: 24px; border-radius: var(--r-s);
    color: var(--c-tx4); font-size: 14px; cursor: pointer; opacity: 0;
    transition: opacity .12s, background .12s, color .12s;
  }
  .t-child:hover .row-act, .t-sec-hdr:hover .row-act,
  .fav-item:hover .row-act, .row-act:focus-visible, .row-act.is-on { opacity: 1; }
  .row-act:hover { background: var(--c-bg5); color: var(--c-tx1); }
  .row-act.is-on { color: var(--c-accent-tx); }
  .row-act-danger:hover { background: var(--c-red-bg); color: var(--c-red-tx); }
  /* Im Abschnittskopf immer sichtbar, aber zurueckhaltend */
  .t-sec-hdr .row-act { opacity: .8; }

  /* A–Z-Sprungleiste */
  .artist-az {
    display: grid; grid-template-columns: repeat(13, 1fr); gap: 2px;
    padding: var(--sp-1) var(--sp-1) var(--sp-2);
  }
  .az-btn {
    height: 22px; padding: 0; border: none; border-radius: var(--r-s);
    background: var(--c-bg5); color: var(--c-tx2);
    font: 600 var(--fs-cap) 'Segoe UI', system-ui, sans-serif; cursor: pointer;
  }
  .az-btn:hover { background: var(--c-act-bg); color: var(--c-accent-tx); }
  .az-btn:disabled { background: none; color: var(--c-tx7); opacity: .5; cursor: default; }

  .t-empty { padding: var(--sp-1) var(--sp-3); font-size: var(--fs-sm); color: var(--c-tx5); font-style: italic; }
  .pl-drop-hover { background: var(--c-green-bg) !important; box-shadow: inset 0 0 0 1px var(--c-green-br); }

  /* Favoriten */
  .fav-wrap { position: relative; }
  .fav-arrow { font-size: 13px; color: var(--c-tx4); flex-shrink: 0; }
  .fav-dropdown {
    position: absolute; left: 0; right: 0; top: calc(100% + 4px); z-index: 200;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-m);
    box-shadow: 0 12px 32px rgba(0,0,0,.45); padding: var(--sp-1); overflow: hidden;
  }
  .fav-item {
    display: flex; align-items: center; gap: var(--sp-2); width: 100%; height: var(--nav-h);
    padding: 0 var(--sp-2); border: none; border-radius: var(--r-s); background: none;
    color: var(--c-tx2); font: var(--fs-body) 'Segoe UI', system-ui, sans-serif; text-align: left; cursor: pointer;
  }
  .fav-item:hover, .fav-item.active { background: var(--c-hover); color: var(--c-tx1); }
  .fav-item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Suchfeld der Navigation */
  .nav-search-wrap {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0;
    margin: 0 var(--sp-2) var(--sp-2); padding: 0 2px 0 var(--sp-2); height: var(--btn-h-sm);
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-m);
  }
  .nav-search-wrap:focus-within { border-color: var(--c-accent); }
  .nav-search-ico { font-size: 14px; color: var(--c-tx4); flex-shrink: 0; }
  .nav-search-input {
    flex: 1; min-width: 0; height: 100%; background: none; border: none;
    color: var(--c-tx1); font: var(--fs-sm) 'Segoe UI', system-ui, sans-serif;
  }
  .nav-search-input:focus { outline: none; }
  .nav-search-input::placeholder { color: var(--c-tx6); }
  .nav-search-wrap .btn { --h: 22px; }

  /* Fuss: Scannen, Analysieren, Fortschritt */
  .nav-footer { flex-shrink: 0; padding: 0 var(--sp-2) var(--sp-2); }
  .nav-sep-footer { margin: 0 0 var(--sp-2); }
  .nav-status { display: flex; align-items: center; gap: var(--sp-1); min-height: 30px; }
  .ns-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .ns-text { font-size: var(--fs-sm); color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
  .ns-idle { color: var(--c-tx4); }
  .ap-bar { height: 3px; background: var(--c-br1); border-radius: 2px; overflow: hidden; }
  .ap-fill { height: 100%; background: var(--c-accent); border-radius: 2px; transition: width .3s ease; }
  .lib-menu { position: fixed; z-index: 500; min-width: 240px; }
  .lib-menu button { display: flex; align-items: center; gap: var(--sp-2); }
  .t-child.excluded, .t-item.excluded { opacity: .45; font-style: italic; }

  /* Griff zum Verbreitern und Einklappen */
  .nav-handle-wrap { position: relative; flex-shrink: 0; width: 10px; }
  .nav-handle { position: absolute; inset: 0; background: var(--c-bg); cursor: col-resize; transition: background .15s; }
  .nav-handle.collapsed { cursor: default; }
  .nav-handle:hover { background: color-mix(in srgb, var(--c-accent) 25%, transparent); }
  .nav-collapse-btn {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 11;
    width: 16px; height: 36px; padding: 0;
    display: flex; align-items: center; justify-content: center;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-s);
    color: var(--c-tx3); font-size: 13px; cursor: pointer;
  }
  .nav-collapse-btn:hover { color: var(--c-accent-tx); border-color: var(--c-accent); }

  /* ── Rechte Seite: Titelliste ─────────────────────────────────────────── */
  .tracks { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

  .toolbar {
    display: flex; align-items: center; gap: 6px var(--sp-2); flex-wrap: wrap;
    padding: var(--sp-2) var(--sp-3); flex-shrink: 0;
    border-bottom: 1px solid var(--c-br1);
  }
  .search-wrap { position: relative; flex: 1 1 200px; min-width: 180px; max-width: 360px; }
  .search-wrap .ti { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); font-size: 15px; color: var(--c-tx4); pointer-events: none; }
  .search { width: 100%; padding-left: 32px; }
  .tb-count { font-size: var(--fs-sm); color: var(--c-tx4); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .tb-spacer { flex: 1; }
  .dupe-hint { color: var(--c-tx3); font-weight: 400; }
  .dupe-hint u { text-underline-offset: 2px; }
  .dupe-info {
    display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; flex-shrink: 0;
    padding: var(--sp-2) var(--sp-3); background: var(--c-act-bg); border-bottom: 1px solid var(--c-br1);
  }
  .dupe-text { flex: 1 1 320px; margin: 0; font-size: var(--fs-body); color: var(--c-tx2); line-height: 1.45; }
  .dupe-text b { color: var(--c-tx1); }
  .quality-info .btn-group { flex-wrap: wrap; }
  .q-n { font-variant-numeric: tabular-nums; opacity: .8; margin-left: 2px; }
  .q-scan { display: inline-flex; align-items: center; gap: 4px; margin-left: 6px; color: var(--c-tx3); font-size: var(--fs-sm); }
  .spinner { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Warn-Spalte der Qualitaets-Ansicht: Titel beginnen buendig, Grund beim Draufzeigen */
  .q-col { width: 70px; flex-shrink: 0; display: flex; align-items: center; gap: 2px; padding-left: 2px; }
  .q-icon { width: 20px; display: flex; justify-content: center; }
  .q-off { opacity: .45; }
  .d-col { width: 56px; flex-shrink: 0; display: flex; align-items: center; gap: 2px; padding-left: 4px; }
  .pl-col { width: 40px; flex-shrink: 0; padding-left: var(--sp-2); color: var(--c-tx4); font-size: var(--fs-sm); font-variant-numeric: tabular-nums; display: flex; align-items: center; }
  .dl-col { width: 92px; flex-shrink: 0; display: flex; align-items: center; gap: 2px; padding-left: var(--sp-2); }
  .dl-date { width: 60px; color: var(--c-tx4); font-variant-numeric: tabular-nums; font-size: var(--fs-sm); }
  .dl-hdr { color: var(--c-tx4); font-size: var(--fs-sm); }
  .row.missing .dl-col .r-btn { text-decoration: none; }
  .d-keep { width: 24px; text-align: center; font-size: 16px; color: var(--c-green-tx); }
  .d-keep-inline { color: var(--c-green-tx); vertical-align: -2px; }
  /* Zeilen-Aktionen: erscheinen beim Drueberfahren */
  .r-btn {
    width: 24px; height: 22px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
    border: none; border-radius: var(--r-s); background: none; color: var(--c-tx3); cursor: pointer;
    font-size: 14px; opacity: 0; transition: opacity .1s;
  }
  .row:hover .r-btn, .r-btn:focus-visible { opacity: 1; }
  .r-btn:hover { background: var(--c-hover); color: var(--c-tx1); }
  .r-danger:hover { background: var(--c-red-bg); color: var(--c-red-tx); }
  .row.grp-start { border-top: 2px solid var(--c-br3); }
  .q-warn { font-size: 15px; color: var(--c-warn-tx); cursor: help; }
  .q-warn.q-bad { color: var(--c-red-tx); }
  .q-tip {
    position: fixed; transform: translateY(-50%); z-index: 1000;
    display: flex; flex-direction: column; gap: 3px;
    width: max-content; max-width: 340px; padding: 8px 10px; border-radius: var(--r-m);
    background: var(--c-bg5); border: 1px solid var(--c-br2); box-shadow: 0 6px 18px rgba(0,0,0,.35);
    pointer-events: none; font-size: var(--fs-sm); color: var(--c-tx2); line-height: 1.4;
  }
  .q-tip-row b { color: var(--c-tx1); }
  .q-tip-hint { margin-top: 2px; font-size: var(--fs-cap); color: var(--c-tx4); }
  .dupe-best { text-decoration: underline dotted; text-underline-offset: 3px; cursor: help; }
  .dupe-acts { display: flex; gap: var(--sp-2); flex-shrink: 0; }
  .allow-list { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: var(--fs-sm); color: var(--c-tx3); }
  .allow-chip {
    display: inline-flex; align-items: center; gap: 4px; padding: 1px 2px 1px 8px;
    border: 1px solid var(--c-br2); border-radius: 999px; color: var(--c-tx2); max-width: 220px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .allow-x { border: none; background: none; color: var(--c-tx4); cursor: pointer; padding: 0 4px; font-size: 12px; border-radius: 999px; }
  .allow-x:hover { color: var(--c-tx1); background: var(--c-hover); }

  .norm-progress, .sel-bar {
    display: flex; align-items: center; gap: var(--sp-2); flex-shrink: 0;
    padding: 6px var(--sp-3); border-bottom: 1px solid var(--c-br1);
    font-size: var(--fs-sm);
  }
  .norm-progress { color: var(--c-green-tx); background: var(--c-green-bg); }
  .sel-bar { background: var(--c-act-bg); }
  .sel-count { color: var(--c-accent-tx); font-weight: 600; }

  /* Spaltenkopf — gehoert sichtbar zur Tabelle: gleicher Grund wie die
     Navigation, kraeftige Unterkante, Etiketten in Grossbuchstaben */
  .col-header-wrap {
    display: flex; flex-shrink: 0; height: 32px; position: relative;
    background: var(--c-bg2); border-bottom: 1px solid var(--c-br2);
  }
  .col-header {
    flex: 1; display: flex; align-items: center; min-width: 0;
    overflow-x: auto; overflow-y: visible; scrollbar-width: none;
  }
  .col-header::-webkit-scrollbar { display: none; }
  .col-btn {
    display: flex; align-items: center; gap: 2px; height: 100%; padding: 0 var(--sp-2);
    background: none; border: none; flex-shrink: 0; overflow: hidden; white-space: nowrap;
    color: var(--c-tx4); font: 700 var(--fs-cap) 'Segoe UI', system-ui, sans-serif;
    letter-spacing: .08em; text-transform: uppercase; cursor: pointer;
    justify-content: flex-end;
  }
  .col-btn.col-first, .col-btn.dl-hdr { justify-content: flex-start; }
  .col-btn:hover { color: var(--c-tx1); background: var(--c-hover); }
  .col-btn.sort-active { color: var(--c-accent-tx); }
  .sort-ico { font-size: 13px; flex-shrink: 0; }
  .col-resize-handle {
    width: 5px; flex-shrink: 0; align-self: stretch; cursor: col-resize;
    background: linear-gradient(var(--c-br2), var(--c-br2)) center / 1px 50% no-repeat;
  }
  .col-resize-handle:hover { background: var(--c-accent); }
  .row .col-resize-handle { background: none; }
  .col-picker-area {
    width: 36px; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
    position: relative; border-left: 1px solid var(--c-br1);
  }
  .col-picker-menu {
    position: absolute; right: 4px; top: calc(100% + 4px); z-index: 200; min-width: 170px;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-m);
    box-shadow: 0 12px 32px rgba(0,0,0,.45); padding: var(--sp-1);
  }
  .col-picker-item {
    display: flex; align-items: center; gap: var(--sp-2); height: 30px; padding: 0 var(--sp-2);
    border-radius: var(--r-s); cursor: pointer; font-size: var(--fs-body); color: var(--c-tx2); user-select: none;
  }
  .col-picker-item:hover { background: var(--c-hover); color: var(--c-tx1); }

  /* Zeilen: klare Hoehe (Dichte), dezente Trennlinie statt Zebra,
     deutliche Hover- und Auswahlfarbe */
  .rows { flex: 1; overflow: auto; }
  .row {
    display: flex; align-items: center; height: var(--row-h); min-width: max-content;
    border-bottom: 1px solid var(--c-br1); cursor: default;
    font-size: var(--row-fs);
  }
  .row:hover { background: var(--c-hover); }
  .row.sel { background: var(--c-sel); box-shadow: inset 3px 0 0 var(--c-blue); }
  .row.sel:hover { background: var(--c-sel); }
  .row.missing { opacity: .5; }
  .row.dupe-copy .cell { color: var(--c-tx5); }
  .row.dupe-copy .c-title-text { font-weight: 400; }
  .row.missing .cell { text-decoration: line-through; }

  .cell {
    flex-shrink: 0; padding: 0 var(--sp-2);
    color: var(--c-tx3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .c-title {
    display: flex; align-items: center; gap: 6px;
    color: var(--c-tx1); font-weight: 600;
  }
  .c-title-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .c-artist { color: var(--c-tx2); }
  .c-folder, .c-comment { color: var(--c-tx4); }
  .num, .cell.num { text-align: right; font-variant-numeric: tabular-nums; color: var(--c-tx3); }
  .c-ext { text-transform: uppercase; font-size: var(--fs-cap); color: var(--c-tx4); }
  .c-key { display: flex; align-items: center; justify-content: flex-end; }
  .lufs-err { color: var(--c-red-tx); font-size: var(--fs-cap); cursor: help; }
  .pc-badge {
    flex-shrink: 0; font-size: var(--fs-cap); font-weight: 600; color: var(--c-accent-tx);
    font-variant-numeric: tabular-nums;
  }

  /* Qualitaetspunkt links */
  .col-pad { width: 16px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
  .qdot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .q-green  { background: var(--c-green-tx); }
  .q-yellow { background: var(--c-warn-tx); }
  .q-red    { background: var(--c-red-tx); }

  .actions { width: 32px; flex-shrink: 0; display: flex; justify-content: center; align-items: center; opacity: 0; transition: opacity .1s; }
  .row:hover .actions, .row.sel .actions { opacity: 1; }
  .row-warn { color: var(--c-warn-tx); --h: 24px; }

  .empty { padding: 40px 20px; text-align: center; color: var(--c-tx4); font-size: var(--fs-body); }
  .yt-fallback {
    display: flex; align-items: center; justify-content: center; gap: var(--sp-3);
    margin: 0 var(--sp-3) var(--sp-2); padding: var(--sp-3) var(--sp-4);
    background: var(--c-act-bg); border: 1px solid color-mix(in srgb, var(--c-accent) 35%, transparent);
    border-radius: var(--r-m);
  }
  .yt-hint { font-size: var(--fs-body); color: var(--c-tx2); }

  /* AcoustID-Ergebnis */
  .id-score  { font-size: var(--fs-body); font-weight: 600; color: var(--c-accent-tx); }
  .id-source { color: var(--c-tx4); font-weight: 400; margin-left: 6px; }
  .id-field  { display: flex; gap: var(--sp-3); align-items: baseline; }
  .id-lbl    { width: 64px; flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx4); }
  .id-val    { font-size: var(--fs-body); color: var(--c-tx1); }
</style>
