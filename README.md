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

- Python 3.10 or higher
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

2. Run tests (when implemented):
```bash
python manage.py test
```

### Static Files

Static files are served from the `static/` directory. In production, run:
```bash
python manage.py collectstatic
```

## License

This project is open source and available for use.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


https://www.unjobnet.org/
https://app.unv.org/?type=online&rows=200&sortField=CountryCode&sortOrder=-1