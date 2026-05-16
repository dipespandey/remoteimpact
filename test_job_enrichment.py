#!/usr/bin/env python
"""
Test script to verify job description/requirements enrichment.
Tests the _ensure_job_details function with various scenarios.
"""
import sys
import os
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "easyclaw.settings")
sys.path.insert(0, "/opt/easyclaw/repo")
django.setup()

from jobs.services.importers.common import _ensure_job_details

def test_scenario(name, payload):
    """Test a single enrichment scenario."""
    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print(f"{'='*70}")
    
    print("INPUT:")
    print(f"  Title: {payload.get('title', 'N/A')}")
    print(f"  Description: {payload.get('description', 'N/A')[:50]}..." if payload.get('description') else "  Description: [EMPTY]")
    print(f"  Requirements: {payload.get('requirements', 'N/A')[:50]}..." if payload.get('requirements') else "  Requirements: [EMPTY]")
    print(f"  Organization: {payload.get('organization_name', 'N/A')}")
    print(f"  Category: {payload.get('category_name', 'N/A')}")
    print(f"  Job Type: {payload.get('job_type', 'N/A')}")
    print(f"  Raw Data Keys: {list(payload.get('raw_data', {}).keys())}")
    
    result = _ensure_job_details(payload)
    
    print("\nOUTPUT:")
    print(f"  Description Length: {len(result.get('description', ''))}")
    print(f"  Description: {result.get('description', '')[:100]}...")
    print(f"  Requirements Length: {len(result.get('requirements', ''))}")
    print(f"  Requirements: {result.get('requirements', '')[:100]}...")
    
    # Validation
    assert result.get('description'), "❌ Description is empty!"
    assert result.get('requirements'), "❌ Requirements is empty!"
    print("\n✓ PASS: Both description and requirements populated")

# Test Case 1: Empty description and requirements
test_scenario(
    "Case 1: Completely empty",
    {
        "source": "idealist",
        "external_id": "1",
        "title": "Senior Developer",
        "description": "",
        "requirements": "",
        "organization_name": "Tech for Good",
        "category_name": "Technology",
        "job_type": "full-time",
        "raw_data": {},
    }
)

# Test Case 2: Description in raw_data
test_scenario(
    "Case 2: Description in raw_data.description_html",
    {
        "source": "climatebase",
        "external_id": "2",
        "title": "Environmental Scientist",
        "description": "",
        "requirements": "",
        "organization_name": "Green Planet",
        "category_name": "Environment",
        "job_type": "full-time",
        "raw_data": {
            "description_html": "<p>We are looking for an environmental scientist to join our team...</p>",
            "employer_name": "Green Planet",
        },
    }
)

# Test Case 3: Requirements in raw_data
test_scenario(
    "Case 3: Description and requirements in raw_data",
    {
        "source": "eightyk",
        "external_id": "3",
        "title": "Policy Analyst",
        "description": "",
        "requirements": "",
        "organization_name": "Good Think Tank",
        "category_name": "Policy",
        "job_type": "full-time",
        "raw_data": {
            "description": "Join our team to shape policy for impact",
            "qualifications": "5+ years experience in policy analysis",
        },
    }
)

# Test Case 4: Only title provided (minimal case)
test_scenario(
    "Case 4: Minimal payload - only title and org",
    {
        "source": "manual",
        "external_id": "4",
        "title": "Program Manager",
        "description": "",
        "requirements": "",
        "organization_name": "Impact Org",
        "category_name": "",
        "job_type": "part-time",
        "raw_data": {},
    }
)

# Test Case 5: Has description, but no requirements
test_scenario(
    "Case 5: Has description, no requirements",
    {
        "source": "idealist",
        "external_id": "5",
        "title": "Community Organizer",
        "description": "We are looking for an experienced community organizer to build grassroots support for our mission.",
        "requirements": "",
        "organization_name": "People Power",
        "category_name": "Social Justice",
        "job_type": "full-time",
        "raw_data": {},
    }
)

# Test Case 6: Has both (should pass through unchanged)
test_scenario(
    "Case 6: Both description and requirements present",
    {
        "source": "reliefweb",
        "external_id": "6",
        "title": "Medical Officer",
        "description": "We seek a qualified medical officer to provide healthcare services in remote areas.",
        "requirements": "MD or equivalent, 3+ years experience, fluent in English",
        "organization_name": "Global Health",
        "category_name": "Health",
        "job_type": "full-time",
        "raw_data": {},
    }
)

# Test Case 7: Empty strings with whitespace
test_scenario(
    "Case 7: Whitespace-only strings",
    {
        "source": "probablygood",
        "external_id": "7",
        "title": "Operations Lead",
        "description": "   ",
        "requirements": "\n\n\t",
        "organization_name": "Efficient Ops",
        "category_name": "Operations",
        "job_type": "full-time",
        "raw_data": {
            "job_description": "Lead our operations team to scale impact",
        },
    }
)

print("\n" + "="*70)
print("ALL TESTS PASSED ✓")
print("="*70)
