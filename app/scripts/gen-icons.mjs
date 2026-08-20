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
