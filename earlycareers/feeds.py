"""Public programme syndication, using the directory's reviewed status policy."""
from datetime import datetime, time, timezone
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from jobs.services.early_careers import opportunities


class EarlyCareersFeed(Feed):
    title = 'Early careers in impact — Remote Impact'
    description = ('Reviewed fellowships, internships and career programmes. '
                   'Includes open applications, vacancy directories and upcoming rounds; '
                   'work location and eligibility vary by programme.')

    def link(self):
        return settings.SITE_URL.rstrip('/') + reverse('jobs:early_careers')

    def feed_url(self):
        return settings.SITE_URL.rstrip('/') + reverse('jobs:early_careers_feed')

    def items(self):
        return sorted(
            (row for row in opportunities() if row['status'] != 'closed'),
            key=lambda row: (row['checked_date'], row['slug']), reverse=True,
        )

    def item_title(self, item):
        return f"{item['name']} — {item['organization']}"

    def item_guid(self, item):
        return self.link() + '#' + item['slug']

    def item_link(self, item):
        campaign = urlencode({'utm_source': 'community_rss', 'utm_medium': 'referral',
                              'utm_campaign': 'early_careers_feed'})
        return self.link() + '?' + campaign + '#' + item['slug']

    def item_updateddate(self, item):
        # This is our editorial review date, never the provider's publication date.
        return datetime.combine(item['checked_date'], time.min, tzinfo=timezone.utc)

    def item_categories(self, item):
        return [item['sector'], item['kind_label'], item['status_label']]

    def item_description(self, item):
        facts = [
            ('Application status', item['status_label']),
            ('Timing', item['timing']),
            ('Funding', item['funding_detail']),
            ('Work format', item['mode_label']),
            ('Location', item['location']),
            ('Eligibility', item['requirements']),
            ('Duration', item['duration']),
            ('Source reviewed', item['checked_date'].isoformat()),
        ]
        if item['deadline_date']:
            deadline = item['deadline_date'].isoformat()
            if item.get('deadline_timezone_label'):
                deadline += ' · ' + item['deadline_timezone_label']
            facts.append(('Deadline', deadline + '; confirm the cutoff with the provider.'))
        if item['review_due']:
            facts.append(('Review overdue', 'Confirm the current intake with the provider.'))
        body = format_html_join('', '<p><strong>{}:</strong> {}</p>', facts)
        source = format_html('<p><a href="{}">Official source</a>. '
                             'Confirm current eligibility and application availability.</p>',
                             item.get('application_url') or item['source_url'])
        return format_html('<p>{}</p>{}{}', item['fit'], body, source)
