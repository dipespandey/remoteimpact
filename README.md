# Remote Impact Job Board

A remote-only job board platform dedicated to connecting passionate professionals with meaningful opportunities at non-profit organizations worldwide.

## Features

- 🎯 **Impact-Focused**: Exclusively for non-profit and impact-oriented organizations
- 🌍 **Remote Only**: All jobs are remote positions
- 🔍 **Advanced Search**: Search by keywords, category, and job type
- 📱 **Modern UI**: Beautiful, responsive design with Tailwind CSS
- ⚡ **Fast & Efficient**: Built with Django for performance and scalability

## Tech Stack

- **Backend**: Django 5.2+
- **Frontend**: Django Templates + Tailwind CSS (via CDN)
- **Database**: SQLite (default, easily configurable for PostgreSQL/MySQL)
- **Package Management**: uv

## Getting Started

### Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) package manager

### Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd remoteimpact
```

2. Install dependencies:
```bash
uv sync
```

3. Run migrations:
```bash
python manage.py migrate
```

4. Create a superuser (optional, for admin access):
```bash
python manage.py createsuperuser
```

5. Run the development server:
```bash
python manage.py runserver
```

6. Visit `http://127.0.0.1:8000/` in your browser

## Admin Interface

Access the Django admin panel at `http://127.0.0.1:8000/admin/` to:
- Add/edit organizations
- Create job categories
- Post and manage job listings
- Feature jobs
- Set expiration dates

## Project Structure

```
remoteimpact/
├── jobboard/          # Django project settings
├── jobs/              # Main jobs app
│   ├── models.py      # Job, Organization, Category models
│   ├── views.py       # View functions
│   ├── admin.py       # Admin configuration
│   └── urls.py        # URL routing
├── templates/         # Django templates
│   ├── base.html      # Base template with Tailwind CSS
│   └── jobs/          # Job-specific templates
├── static/            # Static files (CSS, JS, images)
└── manage.py          # Django management script
```

## Models

- **Organization**: Non-profit organizations posting jobs
- **Category**: Job categories (Education, Healthcare, Environment, etc.)
- **Job**: Job postings with details, requirements, and application links

## Features in Detail

### Job Listings
- Browse all available remote jobs
- Filter by category and job type
- Search by keywords
- Pagination for easy navigation

### Job Details
- Full job description
- Requirements and qualifications
- Organization information
- Direct application links
- Related job suggestions

### Homepage
- Featured job opportunities
- Latest job postings
- Category browsing
- Call-to-action sections

## Color Scheme

The UI mirrors the latest Neonomics aesthetic with deep greens and neon yellows:
- **Primary**: Twilight greens (#041A15 → #3C5D32 gradients)
- **Accent**: Lime / neon highlights (#E6FF64, #8AD425)
- **Secondary**: Aqua tints (#58F6C7) for streaks and CTA glows
- Clean neutrals for typography so content stays legible on the dark canvas.

## Remote Job Importers

Two Algolia-backed sources are available out of the box:

| Source          | Env defaults (override in production) |
|-----------------|----------------------------------------|
| 80,000 Hours    | `EIGHTYK_ALGOLIA_APP_ID=W6KM1UDIB3`<br>`EIGHTYK_ALGOLIA_API_KEY=d1d7f2c8696e7b36837d5ed337c4a319` |
| Idealist.org    | `IDEALIST_ALGOLIA_APP_ID=NSV3AUESS7`<br>`IDEALIST_ALGOLIA_API_KEY=c2730ea10ab82787f2f3cc961e8c1e06` |

> These are public search keys exposed by the respective sites; you can override them with private replicas if needed.

### Running the crawler

Use the management command to ingest jobs:

```bash
python manage.py import_remote_jobs               # fetch everything
python manage.py import_remote_jobs --source 80000hours
python manage.py import_remote_jobs --limit 50    # cap processed jobs
python manage.py import_remote_jobs --dry-run     # parse but don't write
```

Jobs are deduplicated via `(source, external_id)` pairs, and the command can be scheduled (cron, Celery, GitHub Actions, etc.). Newly ingested jobs immediately become searchable and filterable in the existing UI.

## Development

### Adding New Features

1. Create migrations for model changes:
```bash
python manage.py makemigrations
python manage.py migrate
```

2. Prepare the local embedding model once, then run the test suite:
```bash
uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', device='cpu')"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python manage.py test --noinput
```

Tests disable IndexNow notifications and outgoing email. PostgreSQL concurrency
tests require a pgvector-enabled PostgreSQL database supplied through
`DATABASE_URL`; they are skipped under SQLite. CI tests both database backends,
checks migrations and production settings, and verifies the CSS build before deployment.

### Static Files

Build the stylesheet, local variable fonts, HTMX and Alpine from pinned dependencies:
```bash
cd frontend
npm ci
npm run build
```

Commit the generated assets with changes to their dependencies or templates.
Production uses compressed, fingerprinted assets with long-lived caching. Run
this from the repository root (the Docker build also does it):
```bash
python manage.py collectstatic
```

### Page performance

`scripts/profile_pages.py` records cold and warm timings, query counts and slow
SQL for 26 public, employer and seeker page flows. It only accepts an isolated
PostgreSQL database named `remoteimpact_perf` on localhost. Apply migrations to
that database, run the script with `--seed --output before.json` once to create
the synthetic fixture, then run with `--output after.json` to compare changes.
The fixture contains 5,000 jobs, 120 organizations and 240 seekers. Outgoing email
and indexing notifications are disabled, and resource-board data uses a local
fallback. These measurements describe the fixture, not production traffic.

Anonymous page caches vary by cookie and HTMX request and bypass signed-in users.
Dynamic browse pages cache for 60–300 seconds; this bounds catalogue staleness.
Performance regression tests cover query budgets, cache isolation, candidate
privacy, search ordering and atomic quota checks.

## License

This project is open source and available for use.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


https://www.unjobnet.org/
https://app.unv.org/?type=online&rows=200&sortField=CountryCode&sortOrder=-1
