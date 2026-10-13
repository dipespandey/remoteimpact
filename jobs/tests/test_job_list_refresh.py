"""Execute the live-list script against delayed and failed browser requests."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from django.conf import settings
from django.test import SimpleTestCase


class JobListRefreshTests(SimpleTestCase):
    def test_background_refresh_timeout_retry_and_request_races(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for browser refresh regression tests")
        source = (Path(settings.BASE_DIR) / "templates/jobs/job_list.html").read_text()
        script = re.search(r'<script id="job-list-live-script">(.*?)</script>', source, re.S).group(1)
        harness = r'''
const assert = require('assert');
const vm = require('vm');
const SCRIPT = __SCRIPT__;
function setup(pending = false) {
    function element() {
        return { hidden: true, handlers: {}, style: {}, attributes: {},
            addEventListener(name, fn) { this.handlers[name] = fn; },
            setAttribute(name, value) { this.attributes[name] = value; } };
    }
    const form = element();
    form.action = 'https://remoteimpact.org/jobs/';
    form.fields = [];
    const area = element();
    area.innerHTML = 'visible original jobs';
    area.dataset = { personalizationPending: String(pending) };
    const status = element(), text = { textContent: '' }, retry = element();
    status.querySelector = () => text;
    const sort = { value: 'best-match' }, heading = { textContent: 'Jobs' };
    const document = {
        title: 'Jobs',
        getElementById: id => ({'job-results-area': area, 'job-results-status': status,
            'job-results-retry': retry, 'sort-select': sort}[id] || null),
        querySelector: selector => selector === 'form[role="search"]' ? form : heading
    };
    const requests = [], timers = new Map();
    let nextTimer = 0;
    const state = { ignoreAbort: false, currentUrl: null };
    const fetch = (url, options) => new Promise((resolve, reject) => {
        requests.push({url, options, resolve, reject});
        if (!state.ignoreAbort) options.signal.addEventListener('abort', () => {
            const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
        });
    });
    class DOMParser {
        parseFromString(html) {
            return { title: 'Updated jobs', getElementById: () => ({innerHTML: html}),
                     querySelector: () => ({textContent: 'Updated heading'}) };
        }
    }
    class FormData { constructor(form) { this.fields = form.fields; }
        [Symbol.iterator]() { return this.fields[Symbol.iterator](); } }
    const location = { pathname: '/jobs/', search: '?sort=best-match&page=2', href: 'unchanged' };
    vm.runInNewContext(SCRIPT, { document, window: {fetch, DOMParser}, fetch, DOMParser,
        FormData, URLSearchParams, AbortController, location,
        history: {replaceState: (_, __, url) => state.currentUrl = url},
        setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; },
        clearTimeout: id => timers.delete(id) });
    return {form, area, status, text, retry, requests, timers, state, location, sort};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const submit = e => e.form.handlers.submit({preventDefault() {}});
const complete = (r, html) => r.resolve({ok: true, text: async () => html});
function fireTimer(e, delay) {
    const entry = [...e.timers.entries()].find(([id, timer]) => timer.delay === delay);
    assert(entry, 'expected timer ' + delay);
    e.timers.delete(entry[0]); entry[1].fn();
}
(async () => {
    // Cold personal HTML is immediately usable, then refreshes the same page.
    let e = setup(true);
    assert.equal(e.requests.length, 1);
    assert.equal(e.requests[0].url, '/jobs/?sort=best-match&page=2');
    assert.equal(e.requests[0].options.headers['X-Requested-With'], 'fetch');
    assert.equal(e.area.innerHTML, 'visible original jobs');
    assert.equal(e.area.style.opacity, '');
    complete(e.requests[0], 'personalized jobs'); await flush();
    assert.equal(e.area.innerHTML, 'personalized jobs');
    assert.equal(e.area.attributes['aria-busy'], 'false');
    assert(e.status.hidden);
    assert.equal(e.timers.size, 0);

    // A stalled request is bounded; previous rows remain and retry works.
    e = setup(false);
    assert.equal(e.requests.length, 0);
    submit(e); fireTimer(e, 12000); await flush();
    assert(e.requests[0].options.signal.aborted);
    assert.equal(e.area.innerHTML, 'visible original jobs');
    assert.equal(e.area.attributes['aria-busy'], 'false');
    assert.equal(e.area.style.opacity, '');
    assert(!e.retry.hidden && !e.status.hidden);
    assert(e.text.textContent.includes('Showing previous results'));
    assert.equal(e.location.href, 'unchanged');
    e.retry.handlers.click();
    complete(e.requests[1], 'retried jobs'); await flush();
    assert.equal(e.area.innerHTML, 'retried jobs');
    assert(e.status.hidden && e.retry.hidden);

    // Even if the transport ignores cancellation, old results cannot replace
    // newer filters, or clear the busy state of the active refresh.
    e = setup(false); e.state.ignoreAbort = true;
    submit(e);
    e.form.fields = [['country', 'United States']];
    e.form.handlers.change({target: {name: 'country'}});
    fireTimer(e, 350);
    assert(e.requests[0].options.signal.aborted);
    complete(e.requests[0], 'stale jobs'); await flush();
    assert.equal(e.area.innerHTML, 'visible original jobs');
    assert.equal(e.area.attributes['aria-busy'], 'true');
    complete(e.requests[1], 'new filtered jobs'); await flush();
    assert.equal(e.area.innerHTML, 'new filtered jobs');
    assert(e.state.currentUrl.includes('country=United+States'));
    assert(!e.state.currentUrl.includes('best-match'));

    e = setup(false); e.state.ignoreAbort = true;
    submit(e); submit(e);
    complete(e.requests[1], 'latest jobs'); await flush();
    complete(e.requests[0], 'late stale jobs'); await flush();
    assert.equal(e.area.innerHTML, 'latest jobs');
    assert.equal(e.area.attributes['aria-busy'], 'false');

    // Server errors also preserve the list, and an initial retry retains page.
    e = setup(true);
    e.requests[0].resolve({ok: false, status: 503}); await flush();
    assert.equal(e.area.innerHTML, 'visible original jobs');
    assert(e.text.textContent.includes('Showing latest roles'));
    assert(!e.retry.hidden);
    e.retry.handlers.click();
    assert.equal(e.requests[1].url, '/jobs/?sort=best-match&page=2');
    complete(e.requests[1], 'recovered personal jobs'); await flush();
    assert.equal(e.area.innerHTML, 'recovered personal jobs');

    // Explicit Newest survives a sort change; submitting cancels debounce.
    e = setup(false); e.sort.value = 'date';
    e.area.handlers.change({target: {id: 'sort-select'}});
    submit(e);
    assert(e.requests[0].url.includes('sort=date'));
    assert(![...e.timers.values()].some(timer => timer.delay === 0));
    complete(e.requests[0], 'newest jobs'); await flush();
})().catch(error => { console.error(error); process.exit(1); });
'''.replace("__SCRIPT__", json.dumps(script))
        result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
