// Jedes verwendete Icon muss in der Auswahl (src/lib/tabler-icons.css) stecken —
// sonst erscheint ein gefuelltes Quadrat (so in 1.6.0 beim "Bass"-Hinweis).
// Fehlt eins: in scripts/gen-icons.mjs eintragen und `npm run icons`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')

function files(dir) {
  return readdirSync(dir).flatMap(f => {
    const p = path.join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.(svelte|js)$/.test(f) ? [p] : []
  })
}

test('alle verwendeten Icons sind in tabler-icons.css', () => {
  const css = readFileSync(path.join(SRC, 'lib', 'tabler-icons.css'), 'utf8')
  const defined = new Set([...css.matchAll(/^\.(ti-[a-z0-9-]+)/gm)].map(m => m[1]))
  const used = new Set(files(SRC).flatMap(f => [...readFileSync(f, 'utf8').matchAll(/\bti-[a-z0-9-]+/g)].map(m => m[0])))
  const missing = [...used].filter(c => !defined.has(c)).sort()
  assert.deepEqual(missing, [])
})
