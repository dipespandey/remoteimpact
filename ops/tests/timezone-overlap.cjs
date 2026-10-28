// Run: node ops/tests/timezone-overlap.cjs
// Exercise the actual browser component without a DOM or network dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../templates/jobs/tools/timezone_overlap.html'), 'utf8');
const script = source.split('{% csp_script %}<script>')[1].split('</script>')[0];
let factory;
vm.runInNewContext(script, {
  Date, Intl,
  document: {addEventListener: (_, callback) => callback()},
  Alpine: {data: (_, callback) => { factory = callback; }},
  setInterval: () => {},
});
const tool = factory();
tool.tick = Date.parse('2026-10-28T12:00:00Z');
assert.equal(tool.localTime('Asia/Kolkata', 4), '9:30');
tool.selected = ['Asia/Kolkata', 'Europe/London'];
assert.equal(tool.overlapCount(), 2.5);
assert.equal(tool.overlapRange('Asia/Kolkata'), '14:30 – 17:00');
assert.equal(tool.overlapRange('Europe/London'), '9:00 – 11:30');
assert.equal(tool.isWorkHour('Asia/Kolkata', 11), false);
tool.workStart = 22; tool.workEnd = 6;
tool.selected = ['Europe/London', 'Europe/London'];
assert.equal(tool.overlapCount(), 8);
assert.equal(tool.overlapRange('Europe/London'), '0:00 – 6:00, 22:00 – 24:00');
tool.workStart = NaN;
assert.equal(tool.overlapCount(), 0);
tool.workStart = 9; tool.workEnd = 9;
assert.equal(tool.overlapCount(), 0);
tool.workEnd = 17; tool.selected = ['Europe/London', ''];
assert.equal(tool.overlapCount(), 0);
tool.tick = Date.parse('2026-07-01T12:00:00Z');
assert.equal(tool.getOffset('Europe/London'), 60);
tool.tick = Date.parse('2026-10-28T00:00:00Z');
assert.equal(tool.localTime('Europe/London', 0), '0:00');
console.log('Timezone overlap: fractional offsets, intervals, invalid input, DST and midnight passed.');
