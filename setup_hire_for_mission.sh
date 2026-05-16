#!/bin/bash
# Setup script for Hire for Mission feature
# Run: bash setup_hire_for_mission.sh

set -e

echo "🚀 Setting up Hire for Mission..."
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Please activate it first:"
    echo "   source .venv/bin/activate"
    exit 1
fi

# Activate venv
source .venv/bin/activate

echo "✅ Virtual environment activated"
echo ""

# Run migrations
echo "📝 Running database migrations..."
python manage.py makemigrations --noinput
python manage.py migrate

echo "✅ Database migrations completed"
echo ""

# Collect static files
echo "📦 Collecting static files..."
python manage.py collectstatic --noinput

echo "✅ Static files collected"
echo ""

# Run tests
echo "🧪 Running tests..."
python manage.py test jobs.tests.test_hire_for_mission -v 2

echo "✅ Tests passed"
echo ""

echo "🎉 Hire for Mission setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Update jobs/admin.py and add:"
echo "   from .admin_hire_for_mission import *"
echo ""
echo "2. Access the dashboard at:"
echo "   http://localhost:8000/hire-for-mission/"
echo ""
echo "3. Create a new screening session for a job"
echo ""
echo "📚 Documentation:"
echo "   - HIRE_FOR_MISSION_README.md"
echo "   - HIRE_FOR_MISSION_IMPLEMENTATION.md"
