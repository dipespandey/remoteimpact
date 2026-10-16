/* Coarse discovery journeys. Never send queries, briefs, job IDs, emails or CVs. */
(function () {
    'use strict';
    if (navigator.doNotTrack === '1' || ['remoteimpact.org', 'www.remoteimpact.org'].indexOf(location.hostname) === -1) return;
    var paths = {
        '/remote-education-jobs/': 'education',
        '/remote-sustainability-jobs/': 'sustainability',
        '/impact/remote-nonprofit-jobs/': 'nonprofit',
        '/impact/remote-positions-in-nonprofits/': 'nonprofit'
    };
    var groups = ['education', 'sustainability', 'nonprofit'];
    var sources = ['bing', 'google', 'other_search', 'campaign', 'other_referral', 'direct_or_unknown'];
    var key = 'ri_search_destination_v1', now = Date.now(), journey = null;
    function referrer() { try { return new URL(document.referrer); } catch (_) { return null; } }
    function source() {
        if (new URLSearchParams(location.search).has('utm_source')) return 'campaign';
        var ref = referrer(), host = ref ? ref.hostname.toLowerCase() : '';
        if (/(^|\.)bing\.com$/.test(host)) return 'bing';
        if (/(^|\.)google\.[a-z.]+$/.test(host)) return 'google';
        if (/(^|\.)(duckduckgo\.com|search\.yahoo\.com|ecosia\.org)$/.test(host)) return 'other_search';
        return host && host !== location.hostname ? 'other_referral' : 'direct_or_unknown';
    }
    try {
        var stored = JSON.parse(sessionStorage.getItem(key));
        if (stored && groups.indexOf(stored.group) !== -1 && sources.indexOf(stored.source) !== -1 &&
                typeof stored.started === 'number' && stored.started <= now && now - stored.started < 1800000) journey = stored;
    } catch (_) { /* Storage can be disabled; the current page still works. */ }
    var ref = referrer(), incoming = paths[location.pathname];
    if (incoming && (!journey || (ref && ref.origin !== location.origin))) {
        journey = {group: incoming, source: source(), started: now};
        try { sessionStorage.setItem(key, JSON.stringify(journey)); } catch (_) { /* optional */ }
    }
    if (!journey || navigator.doNotTrack === '1') return;
    function emit(name) {
        var safeRef = referrer();
        window.dataLayer = window.dataLayer || [];
        function queue() { window.dataLayer.push(arguments); }
        queue('event', name, {
            event_category: 'search_destination', landing_group: journey.group,
            discovery_source: journey.source,
            page_location: location.origin + location.pathname,
            page_referrer: safeRef ? safeRef.origin : ''
        });
    }
    function viewed() {
        if (document.visibilityState === 'hidden') return;
        document.removeEventListener('visibilitychange', viewed);
        if (incoming) emit('seo_landing_view');
        else if (/^\/jobs\/[^/]+\/$/.test(location.pathname)) emit('seo_job_view');
        else if (location.pathname === '/alerts/preview/') emit('seo_scout_visit');
        else if (location.pathname === '/accounts/signup/') emit('seo_signup_visit');
    }
    if (document.visibilityState === 'hidden') document.addEventListener('visibilitychange', viewed);
    else viewed();
    document.addEventListener('click', function (event) {
        var link = event.target.closest('a');
        if (!link) return;
        var action = link.dataset.track;
        if (['apply_click', 'apply_inplatform', 'apply_email'].indexOf(action) !== -1) emit('seo_apply_click');
        else if (action === 'seo_scout_cta') emit('seo_scout_click');
    });
})();
