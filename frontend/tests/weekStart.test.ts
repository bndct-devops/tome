// Unit tests for the reader's week start across the web's week displays (src/lib/weekStart.ts).
//   npm run test:unit
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { firstJsDay, getWeekStart, inWeekOrder, rowOfJsDay, setWeekStart } from '../src/lib/weekStart.ts'

test('Monday until the user says otherwise; anything unknown is Monday', () => {
  setWeekStart(undefined)
  assert.equal(getWeekStart(), 'monday')
  setWeekStart('friday')
  assert.equal(getWeekStart(), 'monday')
})

test('rows and orders follow the week start', () => {
  setWeekStart('monday')
  assert.equal(firstJsDay(), 1)
  assert.equal(rowOfJsDay(1), 0) // Monday first
  assert.equal(rowOfJsDay(0), 6) // Sunday last
  assert.deepEqual(inWeekOrder(['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']), ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'])
  setWeekStart('sunday')
  assert.equal(firstJsDay(), 0)
  assert.equal(rowOfJsDay(0), 0)
  assert.equal(rowOfJsDay(6), 6)
  assert.equal(rowOfJsDay(1), 1)
  assert.deepEqual(inWeekOrder(['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']), ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'])
  setWeekStart('monday')
})
