// Erzeugt src/lib/tabler-icons.css aus den SVG-Quellen von @tabler/icons.
// Nur die tatsächlich benutzten Icons landen als data-URI-Maske in der CSS —
// so bleibt die App offline-fähig ohne den ~2 MB grossen Icon-Webfont.
// Neues Icon gebraucht? Hier eintragen und `npm run icons` laufen lassen.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC  = join(ROOT, 'node_modules', '@tabler', 'icons', 'icons')

const ICONS = [
  ['clock',          'outline'],
  ['clock-down',     'outline'],
  ['copy',           'outline'],
  ['device-desktop', 'outline'],
  ['download',       'outline'],
  ['folder',         'outline'],
  ['history',        'outline'],
  ['list',           'outline'],
  ['music',          'outline'],
  ['pin',            'outline'],
  ['search',         'outline'],
  ['star',           'outline'],
  ['tags',           'outline'],
  ['user',           'outline'],
  ['vinyl',          'outline'],
  ['pin-filled',     'filled'],
  // Grundsystem 09/2026: Titelleiste, Player, Queue, Werkzeugleisten
  ['settings',        'outline'],
  ['sun',             'outline'],
  ['moon',            'outline'],
  ['terminal-2',      'outline'],
  ['notes',           'outline'],
  ['help',            'outline'],
  ['baseline-density-small', 'outline'],
  ['baseline-density-large', 'outline'],
  ['arrows-shuffle',  'outline'],
  ['repeat',          'outline'],
  ['repeat-once',     'outline'],
  ['dots',            'outline'],
  ['x',               'outline'],
  ['trash',           'outline'],
  ['player-skip-forward', 'outline'],
  ['player-skip-back', 'outline'],
  ['player-track-next', 'outline'],
  ['player-track-prev', 'outline'],
  ['refresh',         'outline'],
  ['plus',            'outline'],
  ['check',           'outline'],
  ['alert-triangle',  'outline'],
  ['chevron-down',    'outline'],
  ['chevron-up',      'outline'],
  ['chevron-right',   'outline'],
  ['chevron-left',    'outline'],
  ['playlist-add',    'outline'],
  ['radio',           'outline'],
  ['wand',            'outline'],
  ['scan',            'outline'],
  ['external-link',   'outline'],
  ['filter',          'outline'],
  ['grip-vertical',   'outline'],
  ['volume',          'outline'],
  ['columns-3',       'outline'],
  ['folder-plus',     'outline'],
  ['minus',           'outline'],
  ['maximize',        'outline'],
  ['player-stop',     'outline'],
  ['send',            'outline'],
  ['device-mobile',   'outline'],
  ['info-circle',     'outline'],
  ['palette',         'outline'],
  ['plug',            'outline'],
  ['adjustments-horizontal', 'outline'],
  ['key',             'outline'],
  ['player-play',     'outline'],
  ['player-play-filled',  'filled'],
  ['player-pause-filled', 'filled'],
]

function maskUrl(name, variant) {
  const file = variant === 'filled' ? name.replace(/-filled$/, '') : name
  const svg = readFileSync(join(SRC, variant, file + '.svg'), 'utf8')
    .replace(/\s*class="[^"]*"/g, '')
    .replace(/currentColor/g, '#000')
    .replace(/\s*\n\s*/g, ' ')
    .trim()
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
}

const version = JSON.parse(
  readFileSync(join(ROOT, 'node_modules', '@tabler', 'icons', 'package.json'), 'utf8')).version

const out = [
  `/* Automatisch erzeugt von scripts/gen-icons.mjs — nicht von Hand bearbeiten.`,
  ` * Quelle: @tabler/icons ${version} (MIT). Neu erzeugen mit: npm run icons */`,
  ``,
  `.ti {`,
  `  display: inline-block;`,
  `  width: 1em;`,
  `  height: 1em;`,
  `  vertical-align: -0.125em;`,
  `  background-color: currentColor;`,
  `  -webkit-mask-repeat: no-repeat;`,
  `          mask-repeat: no-repeat;`,
  `  -webkit-mask-position: center;`,
  `          mask-position: center;`,
  `  -webkit-mask-size: contain;`,
  `          mask-size: contain;`,
  `}`,
  ``,
  ...ICONS.map(([name, variant]) => {
    const u = maskUrl(name, variant)
    return `.ti-${name} { -webkit-mask-image: ${u}; mask-image: ${u}; }`
  }),
  ``,
].join('\n')

writeFileSync(join(ROOT, 'src', 'lib', 'tabler-icons.css'), out)
console.log(`tabler-icons.css: ${ICONS.length} Icons, ${(out.length / 1024).toFixed(1)} KB`)
