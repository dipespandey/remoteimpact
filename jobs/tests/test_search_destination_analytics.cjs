const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../static/search-destination-analytics.js'), 'utf8');
function visit(url, referrer = '', stored = null, dnt = '') {
    const location = new URL(url), handlers = {}, storage = new Map();
    if (stored) storage.set('ri_search_destination_v1', JSON.stringify(stored));
    const window = {dataLayer: []};
    vm.runInNewContext(code, {URL, URLSearchParams, location, window, navigator: {doNotTrack: dnt}, Date,
        document: {referrer, visibilityState: 'visible', addEventListener: (name, handler) => {handlers[name] = handler;}, removeEventListener: () => {}},
        sessionStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)}});
    return {events: window.dataLayer, handlers, stored: () => JSON.parse(storage.get('ri_search_destination_v1') || 'null')};
}
test('landing records a fixed destination and Bing source, never query strings', () => {
    const page = visit('https://remoteimpact.org/remote-education-jobs/?q=private-CV-secret', 'https://www.bing.com/search?q=private-search-secret');
    assert.equal(page.events[0][1], 'seo_landing_view');
    assert.equal(page.events[0][2].landing_group, 'education');
    assert.equal(page.events[0][2].discovery_source, 'bing');
    assert.equal(page.events[0][2].page_referrer, 'https://www.bing.com');
    assert.doesNotMatch(JSON.stringify(page.events), /private|search\?q|CV-secret/);
});
test('a job view and apply click retain the landing across internal navigation', () => {
    const landing = visit('https://remoteimpact.org/remote-sustainability-jobs/', 'https://www.bing.com/search?q=sustainability');
    const job = visit('https://remoteimpact.org/jobs/example/', 'https://remoteimpact.org/remote-sustainability-jobs/?q=private', landing.stored());
    assert.equal(job.events[0][1], 'seo_job_view');
    job.handlers.click({target: {closest: () => ({dataset: {track: 'apply_click'}})}});
    assert.equal(job.events[1][1], 'seo_apply_click');
    assert.equal(job.events[1][2].landing_group, 'sustainability');
    assert.equal(job.events[1][2].discovery_source, 'bing');
});
test('expired or tampered journeys cannot emit arbitrary dimensions', () => {
    for (const stored of [{group: 'education', source: 'bing', started: 0}, {group: 'email@example.com', source: 'bing', started: Date.now()}, {group: 'education', source: 'private-secret', started: Date.now()}]) {
        assert.equal(visit('https://remoteimpact.org/jobs/example/', '', stored).events.length, 0);
    }
});
test('local development and Do Not Track do not create journey storage or events', () => {
    for (const [url, dnt] of [['http://localhost:8000/remote-education-jobs/', ''], ['https://remoteimpact.org/remote-education-jobs/', '1']]) {
        const page = visit(url, '', null, dnt);
        assert.equal(page.events.length, 0); assert.equal(page.stored(), null);
    }
});
test('explicit campaigns are not labelled organic and external entry replaces an old touch', () => {
    const page = visit('https://remoteimpact.org/remote-education-jobs/?utm_source=private-campaign', 'https://www.bing.com/', {group: 'nonprofit', source: 'direct_or_unknown', started: Date.now()});
    assert.equal(page.events[0][2].discovery_source, 'campaign');
    assert.equal(page.events[0][2].landing_group, 'education');
    assert.doesNotMatch(JSON.stringify(page.events), /private-campaign/);
});
