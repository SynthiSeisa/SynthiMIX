// Feste Adresse im Musikwunsch-Code (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { wishHostMismatch } from '../src/lib/wishhost.js'

const st = (o) => ({ running: true, services: { wishes: true }, ip: '10.0.0.7', ...o })

test('andere IP als gedruckt → Warnung', () => {
  assert.equal(wishHostMismatch(st({ wish_host: '192.168.0.50' })), true)
  assert.equal(wishHostMismatch(st({ wish_host: '10.0.0.7' })), false)
})

test('automatisch, Rechnername oder Dienst aus → keine Warnung', () => {
  assert.equal(wishHostMismatch(st({ wish_host: '' })), false)
  assert.equal(wishHostMismatch(st({ wish_host: 'dj-laptop.fritz.box' })), false)
  assert.equal(wishHostMismatch(st({ wish_host: '192.168.0.50', running: false })), false)
  assert.equal(wishHostMismatch(st({ wish_host: '192.168.0.50', services: { wishes: false } })), false)
  assert.equal(wishHostMismatch(null), false)
})
