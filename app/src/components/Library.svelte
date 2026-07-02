<script>
  import { onMount } from 'svelte'
  import { library, scanStatus, playlists, send, normalizeProgress, dlHistory, scanRecursive, playlistContent, appSettings, downloadTree, downloadTreeLoaded, skipNextCrossfade, analyzeProgress, selectionOwner } from '../stores/ws.js'
  import BetterVersionDialog from './BetterVersionDialog.svelte'
  import DuplicateScanDialog from './DuplicateScanDialog.svelte'

  let showDupeScan     = $state(false)
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

  // ── Download directory tree ───────────────────────────────────────────────
  let dlFolderOpen  = $state({})  // folder path → bool

  function loadDlTree() {
    send({ type: 'get_download_tree' })
  }

  function selectNav(mode) {
    navMode = mode
    search  = ''
    selected = new Set()
  }

  // ── Nav section expand/collapse ───────────────────────────────────────────
  let secFsOpen       = $state(true)
  let secArtistOpen   = $state(false)
  let secDlOpen       = $state(true)
  let secPlaylistOpen = $state(true)
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

  function toggleSection(sec) {
    if (sec === 'fs') {
      secFsOpen = !secFsOpen
      if (secFsOpen && !fsReady) loadFsRoots()
    }
    else if (sec === 'artist')   secArtistOpen = !secArtistOpen
    else if (sec === 'dl') {
      secDlOpen = !secDlOpen
      if (secDlOpen && !$downloadTreeLoaded) loadDlTree()
    }
    else if (sec === 'playlist') secPlaylistOpen = !secPlaylistOpen
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

    function addTrack(t) {
      send({ type: 'playlist_add_track', playlist: plPath,
             path: t.path, title: t.title, duration_sec: t.duration_sec ?? 0 })
    }

    const multiRaw = e.dataTransfer.getData('application/x-ytdl-multi')
    if (multiRaw) {
      try { JSON.parse(multiRaw).forEach(addTrack); return } catch {}
    }
    const rich = e.dataTransfer.getData('application/x-ytdl-track')
    if (rich) {
      try { addTrack(JSON.parse(rich)); return } catch {}
    }
    const path = e.dataTransfer.getData('text/plain')
    if (path) {
      const t = $library.find(lt => lt.path === path)
      if (t) addTrack(t)
    }
    // Invalidate cached content so next open re-fetches
    playlistContent.update(m => { const c = {...m}; delete c[plPath]; return c })
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
      return list
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

    // ── History mode: show history items (with library enrichment) ──────────
    if (navMode === 'history') {
      const libByPath = new Map($library.map(t => [t.path, t]))
      let list = $dlHistory
        .filter(h => h.path)
        .map(h => libByPath.get(h.path) ?? {
          path: h.path, title: h.title || h.path.split(/[\\/]/).pop(),
          artist: '', duration_sec: 0, lufs: -99, bpm: 0,
          bitrate_kbps: h.bitrate_kbps ?? 0
        })
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
      const folder = $downloadTree.folders.find(f => f.path === folderPath)
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
      if (navMode === 'duplicates') return dupesAll.has(t.path)
      if (navMode === 'all' && hideDupesAuto) return !dupesHidden.has(t.path)
      return true
    })

    if (q) {
      list = list.filter(t => {
        const { artist, title } = getTrackArtistTitle(t)
        return title.toLowerCase().includes(q) ||
               artist.toLowerCase().includes(q) ||
               (t.folder ?? '').toLowerCase().includes(q)
      })
    }

    return applySort(list)
  })

  let hideDupesAuto = $state(true)

  // Stems are excluded from duplicate detection entirely
  const STEM_RE = /\b(stem|stems|vocal|vocals|instrumental|karaoke|acapella|a[\s-]cappella)\b/i

  const { dupesHidden, dupesAll, dupesGroups } = $derived.by(() => {
    const groups = new Map()  // normalized key → track[]

    for (const t of $library) {
      if (STEM_RE.test(t.title ?? '')) continue   // skip stems

      // Use metadata artist if available, else extract from "Artist - Title" pattern
      const artist = (t.artist ?? '').trim() ||
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

    const hidden = new Set()
    const all    = new Set()
    const groupsList = []  // for duplicate-scan dialog

    for (const tracks of groups.values()) {
      if (tracks.length < 2) continue
      const sorted = [...tracks].sort((a, b) =>
        (b.bitrate_kbps ?? 0) - (a.bitrate_kbps ?? 0) ||
        (b.duration_sec ?? 0) - (a.duration_sec ?? 0)
      )
      sorted.forEach((t, i) => {
        all.add(t.path)
        if (i > 0) hidden.add(t.path)
      })
      groupsList.push({ best: sorted[0], others: sorted.slice(1) })
    }

    return { dupesHidden: hidden, dupesAll: all, dupesGroups: groupsList }
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


  function setSort(key) {
    if (sortCol === key) sortAsc = !sortAsc
    else { sortCol = key; sortAsc = key === 'title' }
  }

  // ── Column system (widths, visibility, order — all persisted) ───────────
  const ALL_COL_DEFS = [
    { key: 'title',        label: 'Titel'            },
    { key: 'artist',       label: 'Künstler'         },
    { key: 'album_artist', label: 'Albumkünstler'    },
    { key: 'folder',       label: 'Ordner'           },
    { key: 'ext',          label: 'Format'           },
    { key: 'duration',     label: 'Dauer'            },
    { key: 'lufs',         label: 'LUFS'             },
    { key: 'bpm',          label: 'BPM'              },
    { key: 'bitrate',      label: 'kbps'             },
    { key: 'comment',      label: 'Kanal'            },
    { key: 'mtime',        label: 'Geändert'         },
  ]
  const COL_DEFAULTS  = { title: 180, artist: 120, album_artist: 110, folder: 100, ext: 40, duration: 46, lufs: 42, bpm: 38, bitrate: 40, comment: 110, mtime: 76 }
  const COL_VIS_DEF   = { title: true, artist: true, album_artist: false, folder: false, ext: false, duration: true, lufs: true, bpm: true, bitrate: true, comment: true, mtime: false }
  const COL_ORDER_DEF = ALL_COL_DEFS.map(c => c.key)

  function _loadColState() {
    try { return JSON.parse(localStorage.getItem('libColState') || '{}') } catch { return {} }
  }
  const _cs = _loadColState()
  let colWidths  = $state({ ...COL_DEFAULTS,  ...(_cs.widths  || {}) })
  let colVisible = $state({ ...COL_VIS_DEF,   ...(_cs.visible || {}) })
  // Gespeicherte Reihenfolge laden, fehlende neue Spalten am Ende anhängen
  const _savedOrder = Array.isArray(_cs.order) ? _cs.order : [...COL_ORDER_DEF]
  const _missingCols = COL_ORDER_DEF.filter(k => !_savedOrder.includes(k))
  let colOrder   = $state([..._savedOrder, ..._missingCols])
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
  function onRowsScroll() { if (_colHeaderEl && _rowsEl) _colHeaderEl.scrollLeft = _rowsEl.scrollLeft }

  // Virtual scrolling
  const ROW_H  = 26
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
  let folderCtx = $state(null)  // { x, y, path }

  function onFolderCtx(e, path) {
    e.preventDefault()
    e.stopPropagation()
    folderCtx = { x: e.clientX, y: e.clientY, path }
  }

  function excludeFolderFromLibrary() {
    if (!folderCtx) return
    const fp = folderCtx.path
    folderCtx = null
    // Normalize path separators and ensure trailing separator for exact prefix match
    const norm = (p) => (p ?? '').replace(/\\/g, '/').replace(/\/$/, '')
    const fpN = norm(fp)
    const toRemove = $library.filter(t => {
      const f = norm(t.folder ?? t.path?.split(/[\\/]/).slice(0, -1).join('/') ?? '')
      return f === fpN || f.startsWith(fpN + '/')
    })
    if (toRemove.length === 0) {
      alert(`Keine Tracks aus diesem Ordner in der Bibliothek gefunden.\nOrdner: ${fp}`)
      return
    }
    if (!confirm(`${toRemove.length} Tracks aus Bibliothek ausschließen (Dateien bleiben)?`)) return
    for (const t of toRemove) send({ type: 'library_remove', path: t.path })
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
    for (const { path } of dlgDeletePaths) send({ type: 'library_remove_disk', path })
    selected = new Set()
    dlgDeletePaths = []
  }

  function qualityDot(track) {
    const ext = track.path?.split('.').pop()?.toLowerCase() ?? ''
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
    if (!confirm(`${dupesHidden.size} doppelte Tracks (niedrigste Qualität) von Festplatte löschen?`)) return
    for (const p of dupesHidden) send({ type: 'library_remove_disk', path: p })
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
    filtered.forEach(t => addToQueue(t))
  }

  function normalizeSelected() {
    const paths = [...selected]
    const lufs = ($appSettings.targetLUFS ?? -14)
    if (!confirm(`${paths.length} Dateien auf ${lufs} LUFS normalisieren? (ändert Dateien auf der Festplatte)`)) return
    send({ type: 'normalize_files', paths, target_lufs: lufs, target_tp: -1.5 })
  }

  function removeSelectedFromLibrary() {
    // redirect: delete from disk (also removes from library)
    removeSelectedFromDisk()
  }

  function analyzeTrack(track) {
    send({ type: 'enrich_track', path: track.path, force: true })
    ctxMenu = null
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
      removeSelectedFromLibrary()
    }
  }

  // ── Nav panel width splitter + collapse ──────────────────────────────────
  let navWidth        = $state(200)
  let navCollapsed    = $state(false)
  let _navWidthSaved  = 200

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
        <span class="t-badge">{$library.length}</span>
      </button>
      <button class="t-item {navMode === 'recent' ? 'active' : ''}" onclick={() => selectNav('recent')}>
        <i class="ti ti-clock t-ico" aria-hidden="true"></i>
        <span class="t-name">Wiedergabe-Verlauf</span>
      </button>
      {#if $dlHistory.length > 0}
        <button class="t-item {navMode === 'history' ? 'active' : ''}" onclick={() => selectNav('history')}>
          <i class="ti ti-history t-ico" aria-hidden="true"></i>
          <span class="t-name">Download-Verlauf</span>
          <span class="t-badge">{$dlHistory.length}</span>
        </button>
      {/if}
      {#if dupesAll.size > 0}
        <button class="t-item {navMode === 'duplicates' ? 'active' : ''}" onclick={() => selectNav('duplicates')}>
          <i class="ti ti-copy t-ico" aria-hidden="true" style="color:#c07020"></i>
          <span class="t-name">Duplikate</span>
          <span class="t-badge">{dupesAll.size}</span>
        </button>
      {/if}
      <div class="nav-sep nav-sep-top"></div>
    </div>

    <!-- Scrollable tree section -->
    <div class="nav-tree">

    <!-- 1. MEIN COMPUTER -->
    <button class="t-sec-hdr" onclick={() => toggleSection('fs')}>
      <span class="t-chevron" class:open={secFsOpen}>›</span>
      <span class="t-sec-label">Mein Computer</span>
    </button>
    {#if secFsOpen}
      {#if fsReady}
        {#each fsRoots as drive}
          {@const dOpen = !!fsOpen[drive.path]}
          <button class="t-child {navMode === 'fs:' + drive.path ? 'active' : ''}"
                  onclick={() => clickFsNode(drive)}
                  oncontextmenu={(e) => onFolderCtx(e, drive.path)}>
            <span class="t-toggle-ico">{dOpen ? '−' : '+'}</span>
            <i class="ti ti-device-desktop t-ico-sm" aria-hidden="true"></i>
            <span class="t-name">{drive.name}</span>
          </button>
          {#if dOpen && fsKids[drive.path]}
            {#each fsKids[drive.path] as child}
              {@const cOpen = !!fsOpen[child.path]}
              <button class="t-child t-d2 {navMode === 'fs:' + child.path ? 'active' : ''}"
                      onclick={() => clickFsNode(child)} title={child.name}
                      oncontextmenu={child.isDir ? (e) => onFolderCtx(e, child.path) : undefined}>
                {#if child.isDir}
                  <span class="t-toggle-ico">{cOpen ? '−' : '+'}</span>
                  <i class="ti ti-folder t-ico-sm" aria-hidden="true"></i>
                {:else}
                  <span class="t-toggle-ico" style="opacity:0">·</span>
                  <i class="ti ti-music t-ico-sm" aria-hidden="true"></i>
                {/if}
                <span class="t-name">{child.name}</span>
              </button>
              {#if child.isDir && cOpen && fsKids[child.path]}
                {#each fsKids[child.path] as grand}
                  <button class="t-child t-d3 {navMode === 'fs:' + grand.path ? 'active' : ''}"
                          onclick={() => clickFsNode(grand)} title={grand.name}>
                    {#if grand.isDir}
                      <span class="t-toggle-ico">+</span>
                      <i class="ti ti-folder t-ico-sm" aria-hidden="true"></i>
                    {:else}
                      <span class="t-toggle-ico" style="opacity:0">·</span>
                      <i class="ti ti-music t-ico-sm" aria-hidden="true"></i>
                    {/if}
                    <span class="t-name">{grand.name}</span>
                  </button>
                {/each}
              {/if}
            {/each}
          {/if}
        {/each}
      {/if}
    {/if}

    <!-- 2. KÜNSTLER -->
    <button class="t-sec-hdr" onclick={() => toggleSection('artist')}>
      <span class="t-chevron" class:open={secArtistOpen}>›</span>
      <span class="t-sec-label">Künstler</span>
      <span class="t-sec-badge">{artists.length}</span>
    </button>
    {#if secArtistOpen}
      {#each artists as artist}
        <button class="t-child {navMode === 'artist:' + artist.toLowerCase() ? 'active' : ''}"
                onclick={() => selectNav('artist:' + artist.toLowerCase())}
                title={artist}>
          <i class="ti ti-user t-ico-sm" aria-hidden="true"></i>
          <span class="t-name">{artist}</span>
        </button>
      {/each}
    {/if}

    <!-- 3. DOWNLOADS -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('dl')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('dl')}>
      <span class="t-chevron" class:open={secDlOpen}>›</span>
      <span class="t-sec-label">Downloads</span>
      {#if $downloadTreeLoaded}
        <span class="t-sec-badge">{$downloadTree.folders.length + $downloadTree.files.length}</span>
      {/if}
      <span class="t-sec-action" title="Aktualisieren" role="button" tabindex="0"
            onclick={(e) => { e.stopPropagation(); loadDlTree() }}
            onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), loadDlTree())}>↺</span>
    </div>
    {#if secDlOpen}
      {#if $downloadTreeLoaded}
        {#each $downloadTree.folders as folder}
          <button class="t-child {navMode === 'dl:' + folder.path ? 'active' : ''}"
                  onclick={() => selectNav('dl:' + folder.path)} title={folder.name}>
            <i class="ti ti-folder t-ico-sm" aria-hidden="true"></i>
            <span class="t-name">{stripTrackNumber(folder.name)}</span>
            <span class="t-badge">{folder.tracks.length}</span>
          </button>
        {/each}
        {#each $downloadTree.files as file}
          <button class="t-child t-d2 {navMode === 'dl:file:' + file.path ? 'active' : ''}"
                  onclick={() => selectNav('dl:file:' + file.path)} title={file.name}>
            <i class="ti ti-music t-ico-sm" aria-hidden="true"></i>
            <span class="t-name">{stripTrackNumber(file.name)}</span>
          </button>
        {/each}
      {/if}
    {/if}

    <!-- 4. PLAYLISTEN -->
    <div class="t-sec-hdr" role="button" tabindex="0"
         onclick={() => toggleSection('playlist')}
         onkeydown={(e) => e.key === 'Enter' && toggleSection('playlist')}>
      <span class="t-chevron" class:open={secPlaylistOpen}>›</span>
      <span class="t-sec-label">Playlisten</span>
      <span class="t-sec-badge">{$playlists.length}</span>
      <span class="t-sec-action" title="Queue als Playlist speichern" role="button" tabindex="0"
            onclick={(e) => { e.stopPropagation(); saveQueueAsPlaylist() }}
            onkeydown={(e) => e.key === 'Enter' && (e.stopPropagation(), saveQueueAsPlaylist())}>+</span>
    </div>
    {#if secPlaylistOpen}
      {#if $playlists.length === 0}
        <div class="t-empty">Noch keine · + zum Speichern</div>
      {:else}
        {#each $playlists as pl}
          <div class="t-child t-pl-row {navMode === 'playlist:' + pl.path ? 'active' : ''}
                      {dragOverPlaylist === pl.path ? 'pl-drop-hover' : ''}"
               role="button" tabindex="0"
               onclick={() => openPlaylistInLibrary(pl)}
               ondragover={(e) => { e.preventDefault(); dragOverPlaylist = pl.path }}
               ondragleave={() => dragOverPlaylist = null}
               ondrop={(e) => dropOnPlaylist(e, pl.path)}
               title={pl.name}>
            <i class="ti ti-list t-ico-sm" aria-hidden="true"></i>
            <span class="t-name">{pl.name}</span>
            {#if $playlistContent[pl.path]}
              <span class="t-badge">{$playlistContent[pl.path].length}</span>
            {/if}
            <button class="t-pl-btn" onclick={(e) => { e.stopPropagation(); loadPlaylistToQueue(pl.path) }}
                    title="In Queue laden">▶</button>
            <button class="t-pl-btn t-pl-del" onclick={(e) => { e.stopPropagation(); deletePlaylist(pl.path) }}
                    title="Löschen">✕</button>
          </div>
        {/each}
      {/if}
    {/if}


    </div><!-- /nav-tree -->

    <!-- ── Scan footer — always visible ──────────────────────────────────── -->
    <div class="nav-footer">
      <div class="nav-sep nav-sep-footer"></div>
      <div class="scan-row">
        <button class="scan-btn" onclick={scanLibrary} title="Bibliotheksordner neu einlesen">⟳ Scannen</button>
        <button class="scan-btn" onclick={() => send({ type: 'analyze_library_meta' })}
                title="LUFS und BPM für alle Tracks berechnen die noch keinen Wert haben">◈ Analysieren</button>
      </div>
      <div class="scan-row">
        <button class="scan-btn rec-btn {$scanRecursive ? 'active' : ''}"
                onclick={() => send({ type: 'set_scan_recursive', enabled: !$scanRecursive })}
                title="Unterordner beim Scannen einschließen">
          {$scanRecursive ? '☑' : '☐'} Unterordner einschließen
        </button>
      </div>
      {#if $analyzeProgress}
        {@const { done, total } = $analyzeProgress}
        {@const pct = total > 0 ? Math.round(done / total * 100) : 0}
        <div class="analyze-progress">
          <div class="ap-bar"><div class="ap-fill" style="width:{pct}%"></div></div>
          <div class="ap-row">
            <span class="ap-label">{done}/{total} analysiert</span>
            <button class="ap-stop" onclick={() => send({ type: 'cancel_analyze' })} title="Analyse abbrechen">⏹</button>
          </div>
        </div>
      {:else if $scanStatus}
        <div class="scan-status">{$scanStatus}</div>
      {/if}
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
      {navCollapsed ? '›' : '‹'}
    </button>
  </div>

  <!-- ── Right: track list ──────────────────────────────────────────────── -->
  <div class="tracks">

    <div class="toolbar">
      <input class="search" type="search" placeholder="Suchen…"
        bind:value={search} />
      <span class="count">{filtered.length} Titel</span>
      {#if navMode.startsWith('playlist:')}
        {@const activePlPath = navMode.slice(9)}
        <button class="add-all-btn" onclick={() => loadPlaylistToQueue(activePlPath)}
                title="Gesamte Playlist in Queue laden">▶ In Queue</button>
      {/if}
      {#if navMode === 'recent' && filtered.length > 0}
        <button class="remove-dupes-btn disk" onclick={clearPlayHistory}
                title="Wiedergabe-Verlauf löschen (alle Abspielzähler zurücksetzen)">
          🗑 Verlauf löschen
        </button>
      {/if}
      {#if navMode === 'all' && dupesAll.size > 0}
        <button class="dupe-toggle {hideDupesAuto ? 'active' : ''}"
                onclick={() => hideDupesAuto = !hideDupesAuto}
                title={hideDupesAuto ? 'Duplikate ausgeblendet – klicken um alle zu zeigen' : 'Alle Duplikate sichtbar'}>
          Dupes {dupesHidden.size}
        </button>
      {/if}
      {#if dupesGroups.length > 0}
        <button class="scan-dupes-btn" onclick={() => showDupeScan = true}
                title="Duplikate überprüfen und einzeln entscheiden">
          Duplikate prüfen ({dupesGroups.length})
        </button>
      {/if}
      {#if plDupesGroups.length > 0}
        <button class="scan-dupes-btn" onclick={() => showPlDupeScan = true}
                title="Duplikate in dieser Playlist prüfen">
          Playlist-Duplikate ({plDupesGroups.length})
        </button>
      {/if}
      {#if navMode === 'duplicates' && dupesHidden.size > 0}
        <button class="remove-dupes-btn disk" onclick={removeHiddenDupes}
                title="Niedrigwertige Kopien von Festplatte löschen">
          🗑 {dupesHidden.size} von Festplatte
        </button>
      {/if}
      {#if filtered.length > 0 && selected.size === 0}
        <button class="add-all-btn" onclick={addAllToQueue} title="Alle gefilterten Tracks zur Queue">+ Alle</button>
      {/if}
    </div>
    {#if $normalizeProgress}
      <div class="norm-progress">
        Normalisierung: {$normalizeProgress.done}/{$normalizeProgress.total} — {$normalizeProgress.current}
      </div>
    {/if}

    {#if selected.size > 0}
      <div class="sel-bar">
        <span class="sel-count">{selected.size} ausgewählt</span>
        <button class="sel-btn" onclick={normalizeSelected} title="Ziel: {$appSettings.targetLUFS ?? -14} LUFS">≋ Normalisieren {$appSettings.targetLUFS ?? -14}L</button>
      </div>
    {/if}

    <div class="col-header-wrap">
      <div class="col-header" bind:this={_colHeaderEl}>
        <div class="col-pad"></div>
        {#each cols as col, ci}
          <button
            class="col-btn {sortCol === col.key ? 'sort-active' : ''}"
            style="width:{colWidths[col.key]}px;flex-shrink:0;{ci === 0 ? 'text-align:left' : ''};position:relative"
            draggable="true"
            ondragstart={(e) => onColDragStart(e, col.key)}
            ondragover={(e) => onColDragOver(e, col.key)}
            ondrop={(e) => onColDrop(e, col.key)}
            onclick={() => setSort(col.key)}>
            {col.label}{sortCol === col.key ? (sortAsc ? ' ↑' : ' ↓') : ''}
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
        <button class="col-picker-btn" onclick={(e) => { e.stopPropagation(); colPickerOpen = !colPickerOpen }} title="Spalten wählen">⋮</button>
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
            <button class="yt-search-btn" onclick={() => send({ type: 'search', query: search })}>⬇ Auf YouTube suchen</button>
          </div>
        {/if}
      {:else}
        <div style="height:{filtered.length * ROW_H}px; position:relative; min-width:max-content">
          <div style="position:absolute; top:{_vStart * ROW_H}px; width:100%">
            {#each _vItems as track, vi (track.path)}
              {@const qdot = qualityDot(track)}
              {@const at = getTrackArtistTitle(track)}
              {@const _even = (_vStart + vi) % 2 === 0}
              <div class="row {_even ? 'even' : 'odd'} {(track.play_count ?? 0) > 0 ? 'played' : ''} {selected.has(track.path) ? 'sel' : ''} {track.missing ? 'missing' : ''}"
                   role="row"
                   draggable="true"
                   ondragstart={(e) => dragStart(e, track)}
                   onclick={(e) => handleRowClick(e, track)}
                   ondblclick={(e) => onCtx(e, track)}
                   oncontextmenu={(e) => onCtx(e, track)}>
                <div class="col-pad">
                  {#if qdot}<span class="qdot {qdot}"></span>{/if}
                </div>
                {#each cols as col}
                  {#if col.key === 'title'}
                    <span class="cell c-title" style="width:{colWidths.title}px" title={track.title}>
                      <span class="c-title-text">{at.title}</span>
                      {#if (track.play_count ?? 0) > 0}<span class="pc-badge">×{track.play_count}</span>{/if}
                    </span>
                  {:else if col.key === 'artist'}
                    <span class="cell c-artist" style="width:{colWidths.artist}px" title={at.artist}>{at.artist}</span>
                  {:else if col.key === 'album_artist'}
                    <span class="cell c-folder" style="width:{colWidths.album_artist}px" title={track.album_artist || ''}>{track.album_artist || ''}</span>
                  {:else if col.key === 'folder'}
                    <span class="cell c-folder" style="width:{colWidths.folder}px" title={track.folder || ''}>{track.folder || ''}</span>
                  {:else if col.key === 'ext'}
                    <span class="cell num" style="width:{colWidths.ext}px;text-transform:uppercase;font-size:9px;color:#3a5878">{track.ext || ''}</span>
                  {:else if col.key === 'duration'}
                    <span class="cell num" style="width:{colWidths.duration}px">{fmt(track.duration_sec)}</span>
                  {:else if col.key === 'lufs'}
                    <span class="cell num" style="width:{colWidths.lufs}px">
                      {#if track.unanalyzable}
                        <span class="lufs-err" title="Datei konnte nicht analysiert werden (korrupt/unlesbar)">✕</span>
                      {:else if (track.lufs ?? -99) > -90}
                        {track.lufs?.toFixed(1)}
                      {/if}
                    </span>
                  {:else if col.key === 'bpm'}
                    <span class="cell num" style="width:{colWidths.bpm}px">{track.bpm || ''}</span>
                  {:else if col.key === 'bitrate'}
                    <span class="cell num" style="width:{colWidths.bitrate}px">{track.bitrate_kbps || ''}</span>
                  {:else if col.key === 'comment'}
                    <span class="cell c-comment" style="width:{colWidths.comment}px" title={track.comment || ''}>{track.comment || ''}</span>
                  {:else if col.key === 'mtime'}
                    <span class="cell num" style="width:{colWidths.mtime}px">{fmtMtime(track.mtime)}</span>
                  {/if}
                  <div class="col-resize-handle" style="flex-shrink:0;pointer-events:none;cursor:default"></div>
                {/each}
                <div class="actions">
                  {#if (track.bitrate_kbps > 0 && track.bitrate_kbps < 128) || ((track.lufs ?? -99) > -90 && track.lufs < -20)}
                    <button onclick={(e) => { e.stopPropagation(); send({ type: 'search', query: track.title }) }} title="Schlechte Qualität – erneut herunterladen" class="warn-btn">⚠</button>
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
<svelte:window onclick={() => { closeCtx(); folderCtx = null; colPickerOpen = false }} oncontextmenu={() => { if (!ctxMenu) return; closeCtx() }} onkeydown={libKey} />

{#if betterVersionTrack}
  <BetterVersionDialog track={betterVersionTrack} onclose={() => betterVersionTrack = null} />
{/if}

{#if showDupeScan}
  <DuplicateScanDialog groups={dupesGroups} onclose={() => showDupeScan = false}
    onremove={(path) => send({ type: 'library_remove_disk', path })} />
{/if}

{#if showPlDupeScan}
  {@const plPath = navMode.slice(9)}
  <DuplicateScanDialog groups={plDupesGroups} onclose={() => showPlDupeScan = false}
    onremove={(path) => send({ type: 'playlist_remove_track', playlist: plPath, path })} />
{/if}

{#if editTrack}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="meta-overlay" onclick={() => editTrack = null} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Metadaten bearbeiten</div>
    <label class="meta-label">Titel
      <input class="meta-input" bind:value={editTrack.title}
             onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') saveMetaEdit(); if (e.key === 'Escape') editTrack = null }} />
    </label>
    <label class="meta-label">Künstler
      <input class="meta-input" bind:value={editTrack.artist}
             onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') saveMetaEdit(); if (e.key === 'Escape') editTrack = null }} />
    </label>
    <div class="meta-hint">Speichert in der Datei (überschreibt ID3-Tags) · Enter zum Speichern</div>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => editTrack = null}>Abbrechen</button>
      <button class="meta-save" onclick={saveMetaEdit}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if plNameDialog}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="meta-overlay" onclick={() => plNameDialog = false} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Playlist speichern</div>
    <label class="meta-label">Name
      <input class="meta-input" bind:value={plNameValue} placeholder="Playlist-Name…"
             onkeydown={(e) => { if (e.key === 'Enter') confirmSavePlaylist(); if (e.key === 'Escape') plNameDialog = false }}
             use:focus />
    </label>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => plNameDialog = false}>Abbrechen</button>
      <button class="meta-save" onclick={confirmSavePlaylist}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if plDeletePath}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="meta-overlay" onclick={() => plDeletePath = null} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Playlist löschen?</div>
    <div class="meta-hint">Diese Aktion kann nicht rückgängig gemacht werden.</div>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => plDeletePath = null}>Abbrechen</button>
      <button class="meta-save" style="background:#7a1a1a;border-color:#9a2a2a"
              onclick={() => { send({ type: 'delete_playlist', path: plDeletePath }); plDeletePath = null }}>Löschen</button>
    </div>
  </div>
</div>
{/if}

{#if dlgDeletePaths.length > 0}
  <div class="meta-overlay" onclick={() => dlgDeletePaths = []}>
    <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
      <div class="meta-title">Von Festplatte löschen</div>
      {#if dlgDeletePaths.length === 1}
        <div class="meta-hint">„{dlgDeletePaths[0].title}" wird dauerhaft gelöscht.</div>
      {:else}
        <div class="meta-hint">{dlgDeletePaths.length} Tracks werden dauerhaft gelöscht.</div>
        <div style="max-height:120px;overflow-y:auto;margin-bottom:12px">
          {#each dlgDeletePaths as t}
            <div style="font-size:11px;color:#5a7898;padding:2px 0">{t.title}</div>
          {/each}
        </div>
      {/if}
      <div class="meta-actions">
        <button class="meta-cancel" onclick={() => dlgDeletePaths = []}>Abbrechen</button>
        <button class="meta-save" style="background:#8b2020;border-color:#6b1010"
                onclick={confirmDeleteFromDisk}>Löschen</button>
      </div>
    </div>
  </div>
{/if}

{#if ctxMenu}
  <div class="ctx-menu" style="left:{Math.min(ctxMenu.x, window.innerWidth - 210)}px;top:{Math.min(ctxMenu.y, window.innerHeight - 220)}px">
    <button onclick={() => { playNow(ctxMenu.track); ctxMenu = null }}>Jetzt abspielen</button>
    <button class="ctx-mix" onclick={() => { mixNow(ctxMenu.track); ctxMenu = null }}>⇌ Mix Now</button>
    <button onclick={() => { playNext(ctxMenu.track); ctxMenu = null }}>Als Nächstes einfügen</button>
    <button onclick={() => { addToQueue(ctxMenu.track); ctxMenu = null }}>Zur Warteschlange</button>
    <div class="ctx-sep"></div>
    <button onclick={() => { window.electron?.openPath(ctxMenu.track.path); ctxMenu = null }}>Im Explorer zeigen</button>
    {#if selected.size > 1 && selected.has(ctxMenu.track.path)}
      <button onclick={analyzeSelected}>{selected.size} Tracks analysieren (BPM · LUFS)</button>
    {:else if ctxMenu.track.unanalyzable}
      <button disabled title="Datei ist korrupt oder nicht lesbar" style="opacity:0.35;cursor:not-allowed">Track analysieren (BPM · LUFS)</button>
    {:else}
      <button onclick={() => analyzeTrack(ctxMenu.track)}>Track analysieren (BPM · LUFS)</button>
    {/if}
    <button onclick={() => openBetterVersion(ctxMenu.track)}>Bessere Version suchen</button>
    <button onclick={() => openMetaEdit(ctxMenu.track)}>Metadaten bearbeiten</button>
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
      <button class="ctx-danger" onclick={removeSelectedFromDisk}>{selected.size} Tracks von Festplatte löschen</button>
    {:else}
      <button class="ctx-danger" onclick={() => removeFromDisk(ctxMenu.track)}>Von Festplatte löschen</button>
    {/if}
  </div>
{/if}

{#if folderCtx}
  <div class="q-ctx" style="left:{folderCtx.x}px;top:{folderCtx.y}px"
       onclick={(e) => e.stopPropagation()}>
    <button onclick={excludeFolderFromLibrary}>Aus Bibliothek ausschließen</button>
  </div>
{/if}

<style>
  .library {
    display: flex;
    flex: 1;
    overflow: hidden;
    height: 100%;
  }

  /* ── Nav panel ─────────────────────────────────────────────────────────── */
  .nav {
    display: flex;
    flex-direction: column;
    background: #040710;
    border-right: 1px solid #0c1624;
    flex-shrink: 0;
    overflow: hidden;
  }
  .nav-top {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    background: #040710;
  }
  .nav-sep-top {
    margin: 5px 6px 1px;
  }
  .nav-tree {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    display: flex;
    flex-direction: column;
  }

  /* ── Option C: Ultra Compact Tree ─────────────────────────────────────── */

  /* Top-level flat items (Alle / Verlauf / Duplikate) */
  .t-item {
    display: flex; align-items: center; gap: 6px;
    width: 100%; padding: 0 8px;
    height: 26px; background: none; border: none;
    color: #a0b8d0; font-size: 11px; text-align: left;
    cursor: pointer; flex-shrink: 0; overflow: hidden;
    border-left: 2px solid transparent;
    transition: color .08s, border-color .08s;
  }
  .t-item:hover { color: #d0e4f4; }
  .t-item.active { color: #e07800; border-left-color: #e07800; background: #090e1a; }

  .t-ico { font-size: 13px; flex-shrink: 0; color: #6080a0; }
  .t-item.active .t-ico { color: #e07800; }

  /* Section headers */
  .nav-sep {
    height: 1px;
    background: linear-gradient(to right, #0c1624 0%, #1a2d46 40%, #0c1624 100%);
    margin: 5px 6px;
    flex-shrink: 0;
  }
  .t-sec-hdr {
    display: flex; align-items: center; gap: 5px;
    width: 100%; padding: 7px 8px 5px;
    background: none; border: none; border-top: 1px solid #0c1624;
    color: #5a7898; font-size: 9px; font-weight: 700;
    letter-spacing: .12em; text-transform: uppercase;
    cursor: pointer; text-align: left; flex-shrink: 0;
    margin-top: 3px; transition: color .1s;
  }
  .t-sec-hdr:hover { color: #90b0cc; }

  .t-chevron {
    font-size: 13px; color: #5a7898; flex-shrink: 0;
    transition: transform .15s; display: inline-block;
  }
  .t-chevron.open { transform: rotate(90deg); }

  .t-sec-label { flex: 1; }
  .t-sec-badge { font-size: 9px; color: #6080a0; font-weight: 400; letter-spacing: 0; text-transform: none; }
  .t-sec-action {
    font-size: 13px; color: #6888a8; background: none; border: none;
    cursor: pointer; padding: 0 2px; line-height: 1;
    transition: color .1s;
  }
  .t-sec-action:hover { color: #e07800; }

  /* Child rows */
  .t-child {
    display: flex; align-items: center; gap: 5px;
    width: 100%; padding: 0 8px 0 14px; height: 23px;
    background: none; border: none; border-left: 2px solid transparent;
    color: #90aac4; font-size: 11px; text-align: left;
    cursor: pointer; flex-shrink: 0; overflow: hidden;
    transition: color .08s, border-color .08s;
  }
  .t-child:hover { color: #c8dff0; }
  .t-child.active { color: #e07800; border-left-color: #e07800; background: #090e1a; }
  .t-d2 { padding-left: 24px; }
  .t-d3 { padding-left: 34px; }

  .t-ico-sm { font-size: 12px; flex-shrink: 0; color: #5a7a98; }
  .t-child.active .t-ico-sm { color: #9a5800; }
  .t-chevron-sm { font-size: 9px; color: #5a7898; flex-shrink: 0; width: 8px; }
  .t-toggle-ico {
    font-size: 11px; font-weight: 700; line-height: 1;
    color: #4a8ab8; flex-shrink: 0; width: 12px; text-align: center;
  }
  .t-child:hover .t-toggle-ico { color: #7ab8e0; }

  .t-name {
    flex: 1; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; min-width: 0;
  }

  .t-badge {
    font-size: 9px; color: #607890; flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
  .t-child.active .t-badge { color: #7a4800; }

  .t-loading { padding: 4px 16px; font-size: 10px; color: #4a6080; }
  .t-empty   { padding: 4px 16px; font-size: 10px; color: #5a7898; font-style: italic; }

  /* Playlist row actions */
  .t-pl-row { position: relative; }
  .t-pl-btn {
    font-size: 10px; color: #6888a8; background: none; border: none;
    cursor: pointer; padding: 0 3px; opacity: 0; transition: opacity .1s, color .1s;
    flex-shrink: 0;
  }
  .t-pl-row:hover .t-pl-btn { opacity: 1; }
  .t-pl-btn:hover { color: #e07800; }
  .t-pl-del:hover { color: #c04030; }

  /* ── Scan footer ──────────────────────────────────────────────────────── */
  .nav-footer {
    flex-shrink: 0;
    background: #040710;
    padding-bottom: 4px;
  }
  .nav-sep-footer {
    margin: 0 0 4px;
  }
  .scan-row {
    display: flex; gap: 6px; padding: 4px 8px 2px;
  }
  .scan-btn {
    flex: 1;
    padding: 5px 6px;
    background: none;
    border: 1px solid #1a2838;
    border-radius: 3px;
    color: #4a6080;
    font-size: 10px;
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s;
    white-space: nowrap;
  }
  .scan-btn:hover { border-color: #e07800; color: #e07800; }

  /* Unterordner toggle — own states so hover never masks on/off */
  .rec-btn { color: #3a5068; }
  .rec-btn:hover { border-color: #2a4060; color: #6a8aaa; }
  .rec-btn.active {
    border-color: #e07800; color: #060a10; background: #e07800; font-weight: 600;
  }
  .rec-btn.active:hover { border-color: #ff9020; color: #060a10; background: #ff9020; }

  .analyze-progress {
    padding: 4px 10px 5px;
  }
  .ap-bar {
    height: 2px; background: #0a1828; border-radius: 2px; overflow: hidden; margin-bottom: 3px;
  }
  .ap-fill {
    height: 100%; background: #e07800; border-radius: 2px;
    transition: width 0.3s ease;
  }
  .ap-row {
    display: flex; align-items: center; gap: 6px;
  }
  .ap-label {
    font-size: 9px; color: #4a6080; white-space: nowrap;
  }
  .ap-stop {
    flex-shrink: 0; width: 15px; height: 15px;
    display: flex; align-items: center; justify-content: center;
    background: none; border: 1px solid #3a2020; border-radius: 3px;
    color: #8a3030; font-size: 7px; cursor: pointer; padding: 0;
    transition: border-color .1s, color .1s, background .1s;
  }
  .ap-stop:hover { border-color: #c04040; color: #ff6060; background: #1a0808; }
  .scan-status {
    font-size: 10px;
    color: #5a7090;
    padding: 2px 10px 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ── Nav resize handle ─────────────────────────────────────────────────── */
  .nav-handle-wrap {
    position: relative;
    flex-shrink: 0;
    width: 14px;
  }
  .nav-handle {
    position: absolute;
    inset: 0;
    background: #0b1220;
    cursor: col-resize;
    transition: background 0.15s;
  }
  .nav-handle.collapsed {
    cursor: default;
    background: #080c14;
  }
  .nav-handle:hover { background: #e0780044; }
  .nav-collapse-btn {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: #0d1828;
    border: 1px solid #1a2a3a;
    border-radius: 3px;
    color: #3a5878;
    font-size: 13px;
    cursor: pointer;
    width: 14px;
    height: 28px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s, background 0.15s, border-color 0.15s;
    z-index: 11;
  }
  .nav-collapse-btn:hover {
    color: #e07800;
    background: #111e30;
    border-color: #e0780055;
  }

  /* ── Right track area ──────────────────────────────────────────────────── */
  .tracks {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border-bottom: 1px solid #0e1828;
    flex-shrink: 0;
  }

  .search {
    flex: 1;
    max-width: 300px;
    background: #0c1220;
    border: 1px solid #1a2838;
    border-radius: 3px;
    color: #c8d8f0;
    font-size: 12px;
    padding: 4px 9px;
    outline: none;
    transition: border-color 0.15s;
  }
  .search:focus { border-color: #e07800; }

  .count {
    font-size: 10px;
    color: #4a6080;
    white-space: nowrap;
  }

  /* ── Column header ─────────────────────────────────────────────────────── */
  .col-header-wrap {
    display: flex;
    flex-shrink: 0;
    height: 22px;
    border-bottom: 1px solid #080d18;
    background: #04070e;
    position: relative;
  }
  .col-header {
    flex: 1;
    display: flex;
    align-items: center;
    overflow-x: auto;
    overflow-y: visible;
    scrollbar-width: none;
    min-width: 0;
  }
  .col-header::-webkit-scrollbar { display: none; }
  .col-picker-area {
    width: 26px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    border-left: 1px solid #0d1828;
  }

  .col-btn {
    background: none;
    border: none;
    /* Dezente rechte Trennlinie — zeigt Spaltengrenze */
    border-right: 1px solid #101e30;
    color: #3a5068;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
    padding: 0 6px;
    height: 100%;
    transition: color 0.1s;
    white-space: nowrap;
    overflow: hidden;
    flex-shrink: 0;
    /* Leichte linke Markierung damit Spaltenanfang klar erkennbar ist */
    box-shadow: inset 1px 0 0 #0a1828;
  }
  .col-btn:first-of-type { box-shadow: none; }
  .col-btn:hover { color: #6a8aaa; background: #060c18; }
  .col-btn.sort-active { color: #e07800; }
  .col-resize-handle {
    width: 4px; flex-shrink: 0; cursor: col-resize;
    background: transparent; align-self: stretch;
    transition: background 0.15s;
    /* Schwacher Strich der die resize-zone andeutet */
    box-shadow: inset 1px 0 0 #0c1828;
  }
  .col-resize-handle:hover { background: #1e3a58; }

  /* ── Track rows ────────────────────────────────────────────────────────── */
  .rows { flex: 1; overflow-y: auto; overflow-x: auto; }

  .empty {
    padding: 40px 20px;
    text-align: center;
    color: #4a6080;
    font-size: 12px;
  }

  .row {
    display: flex;
    align-items: center;
    height: 26px;
    border-bottom: 1px solid #060a12;
    transition: background 0.06s;
    cursor: grab;
    min-width: max-content;
  }
  /* Zebra: jede zweite Zeile leicht abgehoben */
  .row.even { background: #060b1c; }
  .row.odd  { background: #03060e; }
  .row:hover    { background: #0d1826; }
  .row.sel      { background: #0c1830; }
  .row.sel:hover { background: #111e38; }
  .row:active   { cursor: grabbing; opacity: 0.8; }
  .row:hover .actions { opacity: 1; }
  .row.sel .actions   { opacity: 0.7; }
  .row.missing        { opacity: 0.38; }
  .row.missing .cell  { text-decoration: line-through; color: #6a3030; }

  .cell {
    font-size: 12px;
    color: #7a96b0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 6px;
    flex-shrink: 0;
  }
  .c-title-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1; }
  .c-comment, .c-folder { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #4a6080; font-size: 10px; padding: 0 4px; flex-shrink: 0; }
  .col-picker-btn {
    width: 100%; height: 100%; background: none; border: none; cursor: pointer;
    color: #3a5070; font-size: 14px; display: flex; align-items: center; justify-content: center;
  }
  .col-picker-btn:hover { color: #8ab0d0; }
  .col-picker-menu {
    position: absolute; right: 0; top: 22px; z-index: 200;
    background: #0c1828; border: 1px solid #1a2d48; border-radius: 4px;
    padding: 4px 0; min-width: 130px; box-shadow: 0 4px 16px #00000088;
  }
  .col-picker-item {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 10px; cursor: pointer; font-size: 11px; color: #8aabcc;
    user-select: none;
  }
  .col-picker-item:hover { background: #111e38; color: #c0d4ec; }
  .col-picker-item input { accent-color: #2a6ca8; }
  .warn-btn { color: #c07020 !important; border-color: #4a3010 !important; }
  .c-title {
    flex-shrink: 0;
    font-size: 12px;
    font-weight: 500;
    color: #c0d4ec;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 6px;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .c-artist {
    color: #5a7898;
    font-size: 11px;
  }
  .cell.num   { color: #4a6578; text-align: right; font-variant-numeric: tabular-nums; }
  .lufs-err   { color: #7a2020; font-size: 9px; cursor: help; }

  .actions {
    width: 22px;
    flex-shrink: 0;
    display: flex;
    gap: 1px;
    justify-content: center;
    align-items: center;
    opacity: 0;
    transition: opacity 0.1s;
  }
  .actions button {
    background: none;
    border: 1px solid #1a2838;
    border-radius: 2px;
    color: #5a7090;
    font-size: 9px;
    width: 18px; height: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: border-color 0.1s, color 0.1s;
    padding: 0;
  }
  .actions button:hover { border-color: #e07800; color: #e07800; }

  /* ── Quality dot ──────────────────────────────────────────────────────── */
  .col-pad {
    width: 14px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .qdot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .q-green  { background: #2e8a34; box-shadow: 0 0 4px #2e8a3460; }
  .q-yellow { background: #9a8018; box-shadow: 0 0 4px #9a801860; }
  .q-red    { background: #8a2818; box-shadow: 0 0 4px #8a281860; }

  /* ── Duplicate scan / remove buttons ────────────────────────────────── */
  .scan-dupes-btn {
    padding: 3px 10px;
    background: #0c1020;
    border: 1px solid #1a2838;
    border-radius: 3px;
    color: #5a8ab0;
    font-size: 10px;
    cursor: pointer;
    white-space: nowrap;
    transition: border-color .1s, color .1s;
  }
  .scan-dupes-btn:hover { border-color: #4a7898; color: #8ac0e0; }

  .remove-dupes-btn {
    padding: 3px 10px;
    background: #180808;
    border: 1px solid #3a1010;
    border-radius: 3px;
    color: #a04040;
    font-size: 10px;
    cursor: pointer;
    white-space: nowrap;
    transition: border-color .1s, color .1s;
  }
  .remove-dupes-btn:hover { border-color: #c04040; color: #e06060; }
  .remove-dupes-btn.disk { background: #1a0808; border-color: #5a1010; color: #c04040; }
  .remove-dupes-btn.disk:hover { border-color: #e04040; color: #ff6060; background: #200808; }

  /* ── Play count badge ─────────────────────────────────────────────────── */
  .pc-badge {
    font-size: 9px;
    color: #e07800;
    margin-left: 5px;
    opacity: 0.7;
  }

  .pl-drop-hover { background: #0c1e14 !important; outline: 1px solid #4a8060; }

  .pl-del {
    background: none;
    border: none;
    color: #1e2838;
    font-size: 9px;
    cursor: pointer;
    padding: 0 4px;
    margin-left: auto;
    transition: color 0.1s;
    flex-shrink: 0;
  }
  .pl-del:hover { color: #c04040; }

  /* ── Context menu ─────────────────────────────────────────────────────── */
  :global(.ctx-menu) {
    position: fixed;
    z-index: 9999;
    background: #080e1a;
    border: 1px solid #1a2838;
    border-radius: 4px;
    padding: 4px 0;
    min-width: 200px;
    box-shadow: 0 8px 28px rgba(0,0,0,.7);
    pointer-events: all;
  }
  :global(.ctx-menu button) {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 6px 16px;
    background: none;
    border: none;
    color: #6a8aaa;
    font-size: 12px;
    text-align: left;
    cursor: pointer;
    transition: background .08s, color .08s;
    gap: 8px;
    white-space: nowrap;
  }
  :global(.ctx-menu button:hover) { background: #0e1828; color: #c8d8f0; }
  :global(.ctx-sep) { height: 1px; background: #0e1828; margin: 3px 0; }
  :global(.ctx-danger) { color: #8a4040 !important; }
  :global(.ctx-danger:hover) { color: #e06060 !important; background: #1a0808 !important; }
  :global(.ctx-mix) { color: #e07800 !important; }
  :global(.ctx-mix:hover) { color: #ff9020 !important; background: #120a00 !important; }

  /* ── Selection bar ────────────────────────────────────────────────────── */
  .sel-bar { display:flex; align-items:center; gap:8px; padding:4px 10px; background:#0a1020; border-bottom:1px solid #0e1828; flex-shrink:0; }
  .sel-count { font-size:11px; color:#e07800; }
  .sel-btn { background:none; border:1px solid #1a2838; border-radius:3px; color:#5a7a9a; font-size:10px; padding:3px 8px; cursor:pointer; transition:all .1s; }
  .sel-btn:hover { border-color:#4a7aaa; color:#c8d8f0; }

  /* ── Duplicate toggle ─────────────────────────────────────────────────── */
  .dupe-toggle {
    background: #0c1420;
    border: 1px solid #1a2838;
    border-radius: 3px;
    color: #6a8090;
    font-size: 10px;
    padding: 3px 8px;
    cursor: pointer;
    transition: border-color .1s, color .1s;
    white-space: nowrap;
  }
  .dupe-toggle:hover { border-color: #e07800; color: #e07800; }
  .dupe-toggle.active { border-color: #c07020; color: #c07020; }

  /* ── Add-all button ───────────────────────────────────────────────────── */
  .add-all-btn {
    background: #0c1420;
    border: 1px solid #1a2838;
    border-radius: 3px;
    color: #4a7a60;
    font-size: 10px;
    padding: 3px 8px;
    cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .add-all-btn:hover { border-color: #4a9a60; color: #6aba80; }

  /* ── YouTube fallback ─────────────────────────────────────────────────── */
  .yt-fallback {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 20px;
    background: #100c00;
    border: 1px solid #e0780044;
    margin: 0 12px 8px;
    border-radius: 3px;
  }
  .yt-hint { font-size: 11px; color: #5a7090; }
  .yt-search-btn {
    background: #e07800;
    border: none;
    border-radius: 3px;
    color: #fff;
    font-size: 11px;
    padding: 4px 12px;
    cursor: pointer;
    transition: background .1s;
  }
  .yt-search-btn:hover { background: #f08a00; }

  /* ── Normalize progress ───────────────────────────────────────────────── */
  .norm-progress {
    padding: 4px 10px;
    background: #0a1020;
    font-size: 10px;
    color: #4a8a70;
    flex-shrink: 0;
    border-bottom: 1px solid #0e1828;
  }

  /* ── Metadata edit dialog ───────────────────────────────────────────────── */
  .meta-overlay {
    position: fixed; inset: 0; z-index: 2000;
    background: rgba(0,0,0,.65); backdrop-filter: blur(2px);
    display: flex; align-items: center; justify-content: center;
  }
  .meta-dialog {
    background: #070c18; border: 1px solid #1a2838; border-radius: 8px;
    padding: 20px 24px 18px; width: 380px;
    box-shadow: 0 16px 48px rgba(0,0,0,.8);
    display: flex; flex-direction: column; gap: 12px;
  }
  .meta-title {
    font-size: 10px; font-weight: 700; letter-spacing: 2px;
    color: #4a6080; text-transform: uppercase; margin-bottom: 2px;
  }
  .meta-label {
    display: flex; flex-direction: column; gap: 5px;
    font-size: 10px; color: #3a5870; text-transform: uppercase; letter-spacing: 1px;
  }
  .meta-input {
    background: #0c1624; border: 1px solid #1a2838; border-radius: 4px;
    color: #c8d8f0; font-size: 13px; padding: 7px 10px; outline: none;
    transition: border-color .15s;
  }
  .meta-input:focus { border-color: #e07800; }
  .meta-hint { font-size: 10px; color: #2a4060; }
  .meta-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
  .meta-cancel {
    background: none; border: 1px solid #1a2838; border-radius: 4px;
    color: #3a5070; font-size: 11px; padding: 6px 16px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .meta-cancel:hover { border-color: #3a5070; color: #6a90b0; }
  .meta-save {
    background: #e0780015; border: 1px solid #e07800; border-radius: 4px;
    color: #e07800; font-size: 11px; padding: 6px 20px; cursor: pointer;
    font-weight: 600; transition: background .1s;
  }
  .meta-save:hover { background: #e0780030; }
</style>
