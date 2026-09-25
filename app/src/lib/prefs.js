// Darstellung, die nur dieses Geraet betrifft: Theme und Dichte.
// Liegt im localStorage und als Attribut am <html>, damit das CSS in App.svelte
// (Farben) und lib/ui.css (Dichte) sofort greift. Titelleiste und
// Einstellungen schalten ueber dieselben Stores um.
import { writable } from 'svelte/store'

function pref(storageKey, attr, fallback, allowed) {
  let value = fallback
  try { value = localStorage.getItem(storageKey) || fallback } catch {}
  if (!allowed.includes(value)) value = fallback
  const store = writable(value)
  store.subscribe(v => {
    document.documentElement.setAttribute(attr, v)
    try { localStorage.setItem(storageKey, v) } catch {}
  })
  return store
}

export const theme   = pref('synthimix-theme',   'data-theme',   'dark',    ['dark', 'light'])
// Kompakt: Pflege zuhause, viele Titel auf einmal. Komfortabel: live, aus Abstand.
export const density = pref('synthimix-density', 'data-density', 'compact', ['compact', 'comfortable'])
