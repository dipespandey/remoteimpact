"""
Comprehensive tests for Hire for Mission screening and evaluation system.
"""
import uuid
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from jobs.models import (
    Organization,
    Job,
    Category,
    Application,
    ScreeningSession,
    ScreeningCandidate,
    TeamFeedback,
)

User = get_user_model()


class ScreeningSessionModelTests(TestCase):
    """Tests for ScreeningSession model."""
    
    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
    
    def test_screening_session_creation(self):
        """Test creating a screening session."""
        session = ScreeningSession.objects.create(
            job=self.job,
            status=ScreeningSession.Status.DRAFT,
            skills_weight=40,
            mission_weight=35,
            culture_weight=25,
        )
        
        self.assertEqual(session.job, self.job)
        self.assertEqual(session.status, ScreeningSession.Status.DRAFT)
        self.assertEqual(session.get_total_applicants(), 0)
        self.assertEqual(session.get_qualified_count(), 0)
    
    def test_screening_session_with_candidates(self):
        """Test screening session with candidates."""
        session = ScreeningSession.objects.create(
            job=self.job,
            status=ScreeningSession.Status.ACTIVE,
        )
        
        applicant1 = User.objects.create_user(
            email="candidate1@example.com",
            password="pass123",
        )
        applicant2 = User.objects.create_user(
            email="candidate2@example.com",
            password="pass123",
        )
        
        app1 = Application.objects.create(
            job=self.job,
            applicant=applicant1,
        )
        app2 = Application.objects.create(
            job=self.job,
            applicant=applicant2,
        )
        
        cand1 = ScreeningCandidate.objects.create(
            session=session,
            application=app1,
            skills_score=80,
            mission_score=85,
            culture_score=75,
            combined_score=80,
        )
        cand2 = ScreeningCandidate.objects.create(
            session=session,
            application=app2,
            skills_score=60,
            mission_score=65,
            culture_score=70,
            combined_score=65,
        )
        
        self.assertEqual(session.get_total_applicants(), 2)
        self.assertEqual(session.get_qualified_count(), 1)  # Only cand1 >= 70


class ScreeningCandidateModelTests(TestCase):
    """Tests for ScreeningCandidate model."""
    
    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(
            job=self.job,
            skills_weight=40,
            mission_weight=35,
            culture_weight=25,
        )
        
        self.applicant = User.objects.create_user(
            email="candidate@example.com",
            password="pass123",
        )
        
        self.application = Application.objects.create(
            job=self.job,
            applicant=self.applicant,
        )
    
    def test_candidate_creation(self):
        """Test creating a screening candidate."""
        candidate = ScreeningCandidate.objects.create(
            session=self.session,
            application=self.application,
            skills_score=85,
            mission_score=90,
            culture_score=80,
            combined_score=86,
        )
        
        self.assertEqual(candidate.application.applicant.email, "candidate@example.com")
        self.assertEqual(candidate.combined_score, 86)
        self.assertEqual(candidate.team_recommendation, ScreeningCandidate.Recommendation.PENDING)
    
    def test_calculate_combined_score(self):
        """Test combined score calculation."""
        candidate = ScreeningCandidate.objects.create(
            session=self.session,
            application=self.application,
            skills_score=100,
            mission_score=80,
            culture_score=60,
        )
        
        # Expected: (100 * 40 + 80 * 35 + 60 * 25) / 100 = 8100 / 100 = 81
        expected_score = int((100 * 40 + 80 * 35 + 60 * 25) / 100)
        calculated = candidate.calculate_combined_score()
        self.assertEqual(calculated, expected_score)
    
    def test_candidate_with_concerns(self):
        """Test candidate flagged with concerns."""
        candidate = ScreeningCandidate.objects.create(
            session=self.session,
            application=self.application,
            skills_score=95,
            mission_score=85,
            culture_score=75,
            combined_score=86,
            has_concerns=True,
            concerns_text="Limited industry experience",
        )
        
        self.assertTrue(candidate.has_concerns)
        self.assertEqual(candidate.concerns_text, "Limited industry experience")


class TeamFeedbackModelTests(TestCase):
    """Tests for TeamFeedback model."""
    
    def setUp(self):
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(job=self.job)
        
        self.applicant = User.objects.create_user(
            email="candidate@example.com",
            password="pass123",
        )
        
        self.application = Application.objects.create(
            job=self.job,
            applicant=self.applicant,
        )
        
        self.candidate = ScreeningCandidate.objects.create(
            session=self.session,
            application=self.application,
            combined_score=80,
        )
    
    def test_team_feedback_creation(self):
        """Test creating team feedback."""
        feedback = TeamFeedback.objects.create(
            candidate=self.candidate,
            reviewer=self.user,
            rating=TeamFeedback.ScoreChoices.EXCELLENT,
            comment="Great candidate!",
            recommendation=ScreeningCandidate.Recommendation.STRONG_YES,
        )
        
        self.assertEqual(feedback.rating, 5)
        self.assertEqual(feedback.comment, "Great candidate!")
        self.assertEqual(feedback.recommendation, ScreeningCandidate.Recommendation.STRONG_YES)
    
    def test_unique_feedback_per_reviewer(self):
        """Test that each reviewer can only submit one feedback per candidate."""
        TeamFeedback.objects.create(
            candidate=self.candidate,
            reviewer=self.user,
            rating=TeamFeedback.ScoreChoices.GOOD,
        )
        
        # Try to create duplicate feedback (should update)
        feedback, created = TeamFeedback.objects.update_or_create(
            candidate=self.candidate,
            reviewer=self.user,
            defaults={
                "rating": TeamFeedback.ScoreChoices.EXCELLENT,
            }
        )
        
        self.assertFalse(created)
        self.assertEqual(feedback.rating, 5)


class HireForMissionViewTests(TestCase):
    """Tests for Hire for Mission views."""
    
    def setUp(self):
        self.client = Client()
        
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(
            job=self.job,
            status=ScreeningSession.Status.ACTIVE,
        )
    
    def test_dashboard_view_requires_login(self):
        """Test that dashboard view requires authentication."""
        response = self.client.get(reverse("jobs:hire_dashboard"))
        self.assertEqual(response.status_code, 302)  # Redirect to login
    
    def test_dashboard_view_authenticated(self):
        """Test dashboard view for authenticated user."""
        self.client.login(email="test@example.com", password="testpass123")
        response = self.client.get(reverse("jobs:hire_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hire for Mission")
    
    def test_shortlist_view_requires_permission(self):
        """Test that shortlist view requires org membership."""
        other_user = User.objects.create_user(
            email="other@example.com",
            password="testpass123",
        )
        self.client.login(email="other@example.com", password="testpass123")
        
        response = self.client.get(
            reverse("jobs:hire_shortlist", args=[self.session.id])
        )
        self.assertEqual(response.status_code, 404)
    
    def test_shortlist_view_with_permission(self):
        """Test shortlist view with proper permissions."""
        self.client.login(email="test@example.com", password="testpass123")
        response = self.client.get(
            reverse("jobs:hire_shortlist", args=[self.session.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shortlist")
    
    def test_analytics_view_with_permission(self):
        """Test analytics view with proper permissions."""
        self.client.login(email="test@example.com", password="testpass123")
        response = self.client.get(
            reverse("jobs:hire_analytics", args=[self.session.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics")


class ShortlistFilteringTests(TestCase):
    """Tests for shortlist filtering functionality."""
    
    def setUp(self):
        self.client = Client()
        
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(
            job=self.job,
            status=ScreeningSession.Status.ACTIVE,
        )
        
        # Create multiple candidates
        for i in range(5):
            applicant = User.objects.create_user(
                email=f"candidate{i}@example.com",
                password="pass123",
            )
            app = Application.objects.create(
                job=self.job,
                applicant=applicant,
            )
            ScreeningCandidate.objects.create(
                session=self.session,
                application=app,
                combined_score=50 + (i * 10),
                has_concerns=i % 2 == 0,
            )
        
        self.client.login(email="test@example.com", password="testpass123")
    
    def test_filter_by_score_range(self):
        """Test filtering candidates by score range."""
        response = self.client.get(
            reverse("jobs:hire_shortlist", args=[self.session.id]),
            {"min_score": "60", "max_score": "80"},
        )
        self.assertEqual(response.status_code, 200)
    
    def test_filter_by_concerns(self):
        """Test filtering candidates with concerns."""
        response = self.client.get(
            reverse("jobs:hire_shortlist", args=[self.session.id]),
            {"has_concerns": "true"},
        )
        self.assertEqual(response.status_code, 200)


class AccessibilityTests(TestCase):
    """Tests for accessibility features (WCAG 2.1 AA)."""
    
    def setUp(self):
        self.client = Client()
        
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(job=self.job)
        self.client.login(email="test@example.com", password="testpass123")
    
    def test_dashboard_has_semantic_html(self):
        """Test that dashboard uses semantic HTML."""
        response = self.client.get(reverse("jobs:hire_dashboard"))
        self.assertEqual(response.status_code, 200)
        # Check for semantic elements
        content = response.content.decode()
        self.assertIn("<header", content.lower() or "<h1", content)
        self.assertIn("<main", content.lower() or "<div", content)


class ResponsiveDesignTests(TestCase):
    """Tests for responsive design."""
    
    def setUp(self):
        self.client = Client()
        
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.org.members.add(self.user)
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="software-engineer",
            organization=self.org,
            category=self.category,
            poster=self.user,
        )
        
        self.session = ScreeningSession.objects.create(job=self.job)
        self.client.login(email="test@example.com", password="testpass123")
    
    def test_mobile_viewport_meta_tag(self):
        """Test that pages include mobile viewport meta tag."""
        response = self.client.get(reverse("jobs:hire_dashboard"))
        content = response.content.decode()
        self.assertIn("viewport", content.lower())
    
    def test_tailwind_responsive_classes(self):
        """Test that templates use Tailwind responsive classes."""
        response = self.client.get(reverse("jobs:hire_dashboard"))
        content = response.content.decode()
        # Check for responsive Tailwind classes
        self.assertIn("md:", content)  # Tailwind breakpoint
