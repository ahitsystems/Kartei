import assert from 'node:assert/strict'
import { test } from 'node:test'
import { telHref } from '../src/lib/tel.ts'

test('tel: links without settings keep the number', () => {
  assert.equal(telHref('+49 511 123-45'), 'tel:+4951112345')
  assert.equal(telHref('(0511) 1234'), 'tel:05111234')
})

test('tel: links through a phone system', () => {
  const p = { outside: '0', plus: '00' }
  assert.equal(telHref('+49 511 1234567', p), 'tel:000495111234567')
  assert.equal(telHref('0511 1234567', p), 'tel:005111234567') // national: outside line only
  assert.equal(telHref('123', p), 'tel:123') // extension: internal call
  assert.equal(telHref('+49 511 1', { outside: '', plus: '00' }), 'tel:00495111')
  assert.equal(telHref('+49 511 1', { outside: '0', plus: '' }), 'tel:0+495111')
})
