# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Expose port
EXPOSE 8000

# Run with gunicorn
CMD ["uv", "run", "gunicorn", "jobboard.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
