// Erklaerungen hinter ein ⓘ legen: Die langen Hinweistexte (.hint) einer
// Einstellungsgruppe werden ausgeblendet, am Gruppentitel erscheint ein ⓘ.
// Draufzeigen zeigt den Text als schwebenden Hinweis, ein Klick heftet ihn an.
//
// Bleiben sichtbar:
// - .hint.keep (Statusmeldungen, z. B. "Lade die Aenderungen…")
// - Gruppen, deren einziger Inhalt der Hinweis ist (sonst stuende nur ein Titel da)
// Der Hinweis bleibt im DOM und wird von Svelte weiter aktualisiert; der
// schwebende Text wird bei jedem Oeffnen frisch daraus kopiert.

let tip = null          // { el, btn, pinned }

function hideTip() {
  if (!tip) return
  tip.el.remove()
  tip.btn.setAttribute('aria-expanded', 'false')
  tip = null
}

function showTip(btn, group, pinned) {
  if (tip && tip.btn === btn) { tip.pinned = tip.pinned || pinned; return }
  hideTip()
  const el = document.createElement('div')
  el.className = 'info-tip'
  el.setAttribute('role', 'tooltip')
  for (const h of group.querySelectorAll('.hint.hint-tip')) {
    if (!h.textContent.trim()) continue
    const p = document.createElement('div')
    p.className = 'info-tip-p'
    p.innerHTML = h.innerHTML          // eigener Inhalt der App, keine Fremddaten
    el.appendChild(p)
  }
  if (!el.childNodes.length) return
  document.body.appendChild(el)
  const r = btn.getBoundingClientRect()
  const w = el.offsetWidth, h = el.offsetHeight
  let left = Math.min(r.left - 8, window.innerWidth - w - 12)
  let top = r.bottom + 6
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6)
  el.style.left = Math.max(8, left) + 'px'
  el.style.top = top + 'px'
  btn.setAttribute('aria-expanded', 'true')
  tip = { el, btn, pinned }
}

function process(root) {
  for (const g of root.querySelectorAll('.group')) {
    const title = g.querySelector(':scope > .group-title')
    if (!title) continue
    const hints = [...g.querySelectorAll('.hint')].filter(h => !h.classList.contains('keep') && !h.classList.contains('inline'))
    const other = [...g.children].filter(c => c !== title && !c.classList.contains('hint'))
    if (!hints.length || !other.length) continue
    for (const h of hints) if (!h.classList.contains('hint-tip')) h.classList.add('hint-tip')
    if (title.querySelector('.info-btn')) continue
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'info-btn'
    btn.title = ''
    btn.setAttribute('aria-label', 'Erklärung anzeigen')
    btn.setAttribute('aria-expanded', 'false')
    btn.innerHTML = '<i class="ti ti-info-circle" aria-hidden="true"></i>'
    btn.addEventListener('mouseenter', () => showTip(btn, g, false))
    btn.addEventListener('mouseleave', () => { if (tip && tip.btn === btn && !tip.pinned) hideTip() })
    btn.addEventListener('focus', () => showTip(btn, g, false))
    btn.addEventListener('blur', () => { if (tip && tip.btn === btn && !tip.pinned) hideTip() })
    btn.addEventListener('click', (e) => {
      e.stopPropagation()
      if (tip && tip.btn === btn && tip.pinned) hideTip()
      else { hideTip(); showTip(btn, g, true) }
    })
    title.appendChild(btn)
  }
}

/** Svelte-Action fuer den Inhaltsbereich der Einstellungen. */
export function infoHints(node) {
  let raf = 0
  const run = () => { raf = 0; process(node) }
  const mo = new MutationObserver(() => { if (!raf) raf = requestAnimationFrame(run) })
  process(node)
  mo.observe(node, { childList: true, subtree: true })
  const onDoc = (e) => { if (tip && !tip.el.contains(e.target)) hideTip() }
  const onScroll = () => hideTip()
  document.addEventListener('click', onDoc)
  node.addEventListener('scroll', onScroll, true)
  return {
    destroy() {
      mo.disconnect()
      if (raf) cancelAnimationFrame(raf)
      document.removeEventListener('click', onDoc)
      node.removeEventListener('scroll', onScroll, true)
      hideTip()
    },
  }
}
