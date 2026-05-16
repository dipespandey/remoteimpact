"""
Unit tests for Hire for Mission screening and evaluation system.
Tests question generation, application scoring, and API endpoints.
"""

import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from jobs.models import (
    Job,
    Organization,
    Category,
    Application,
    MissionScreeningQuestion,
    JobApplicationResponse,
    ApplicationScore,
    HiringFeedback,
)
from jobs.services.question_generator import MissionQuestionGenerator, QuestionGenerationError
from jobs.services.scoring_engine import CandidateScoringEngine, ScoringError

User = get_user_model()


class QuestionGeneratorTestCase(TestCase):
    """Test suite for MissionQuestionGenerator."""

    def setUp(self):
        """Set up test fixtures."""
        self.org = Organization.objects.create(
            name="Test Impact Org",
            slug="test-org",
            description="An org doing good",
            impact_statement="We save lives",
        )
        
        self.category = Category.objects.create(
            name="Healthcare",
            slug="healthcare",
        )
        
        self.job = Job.objects.create(
            title="Health Program Officer",
            slug="health-officer",
            organization=self.org,
            category=self.category,
            description="Manage health programs",
            requirements="Experience with public health",
            impact="Improve patient outcomes",
            skills=["public health", "project management"],
        )
        
        self.generator = MissionQuestionGenerator()

    @patch('jobs.services.question_generator.AIClient.generate')
    def test_generate_questions_success(self, mock_generate):
        """Test successful question generation."""
        mock_response = json.dumps([
            {
                "type": "mission_alignment",
                "text": "Why are you passionate about our mission?"
            },
            {
                "type": "culture_fit",
                "text": "How do you work in collaborative teams?"
            },
            {
                "type": "skills_validation",
                "text": "What public health experience do you have?"
            }
        ])
        
        mock_generate.return_value = mock_response
        
        questions = self.generator.generate_questions_for_job(
            job=self.job,
            num_questions=3,
        )
        
        self.assertEqual(len(questions), 3)
        self.assertEqual(questions[0].question_type, "mission_alignment")
        self.assertEqual(questions[1].question_type, "culture_fit")
        self.assertEqual(questions[2].question_type, "skills_validation")
        
        # Check all questions are in DB
        self.assertEqual(self.job.screening_questions.count(), 3)

    @patch('jobs.services.question_generator.AIClient.generate')
    def test_question_generation_caching(self, mock_generate):
        """Test that questions are cached."""
        mock_response = json.dumps([
            {
                "type": "mission_alignment",
                "text": "Why join us?"
            }
        ])
        
        mock_generate.return_value = mock_response
        
        # First call
        questions1 = self.generator.generate_questions_for_job(self.job, num_questions=1)
        call_count_1 = mock_generate.call_count
        
        # Second call should use cache
        questions2 = self.generator.get_or_generate(self.job)
        call_count_2 = mock_generate.call_count
        
        # Should not have made a second LLM call
        self.assertEqual(call_count_1, call_count_2)
        self.assertEqual(len(questions1), len(questions2))

    @patch('jobs.services.question_generator.AIClient.generate')
    def test_force_regenerate(self, mock_generate):
        """Test force regeneration bypasses cache."""
        mock_response = json.dumps([
            {"type": "mission_alignment", "text": "Why us?"}
        ])
        
        mock_generate.return_value = mock_response
        
        # First generation
        self.generator.generate_questions_for_job(self.job, num_questions=1)
        
        # Force regenerate
        self.generator.regenerate_for_job(self.job)
        
        # Should have called LLM twice (not using cache on second call)
        self.assertEqual(mock_generate.call_count, 2)

    @patch('jobs.services.question_generator.AIClient.generate')
    def test_invalid_json_response(self, mock_generate):
        """Test handling of invalid JSON from LLM."""
        mock_generate.return_value = "Not valid JSON"
        
        with self.assertRaises(QuestionGenerationError):
            self.generator.generate_questions_for_job(self.job)

    @patch('jobs.services.question_generator.AIClient.generate')
    def test_empty_questions_response(self, mock_generate):
        """Test handling of empty questions from LLM."""
        mock_generate.return_value = "[]"
        
        with self.assertRaises(QuestionGenerationError):
            self.generator.generate_questions_for_job(self.job)


class ScoringEngineTestCase(TestCase):
    """Test suite for CandidateScoringEngine."""

    def setUp(self):
        """Set up test fixtures."""
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
            description="A test organization",
            impact_statement="Making impact",
        )
        
        self.category = Category.objects.create(
            name="Education",
            slug="education",
        )
        
        self.job = Job.objects.create(
            title="Teacher",
            slug="teacher",
            organization=self.org,
            category=self.category,
            description="Teach students",
            requirements="Education background",
            skills=["teaching", "curriculum"],
        )
        
        self.applicant = User.objects.create_user(
            email="applicant@test.com",
            password="testpass123",
        )
        
        self.application = Application.objects.create(
            job=self.job,
            applicant=self.applicant,
            cover_letter="I'm passionate about education",
        )
        
        self.engine = CandidateScoringEngine()

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_score_application_success(self, mock_generate):
        """Test successful application scoring."""
        mock_response = json.dumps({
            "mission_alignment": 85,
            "skills_match": 75,
            "culture_fit": 80,
            "recommendation_reason": "Strong mission fit",
            "breakdown": {
                "mission_strengths": ["Passionate about education"],
                "culture_fit_observations": "Good fit for team"
            }
        })
        
        mock_generate.return_value = mock_response
        
        score = self.engine.score_application(self.application)
        
        self.assertEqual(float(score.mission_alignment_score), 85)
        self.assertEqual(float(score.skills_match_score), 75)
        self.assertEqual(float(score.culture_fit_score), 80)
        self.assertEqual(score.recommendation, "strong_yes")
        
        # Check it's in the DB
        saved_score = ApplicationScore.objects.get(application=self.application)
        self.assertEqual(saved_score.id, score.id)

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_low_mission_alignment_downgrades_recommendation(self, mock_generate):
        """Test that low mission alignment downgrades recommendation."""
        mock_response = json.dumps({
            "mission_alignment": 25,  # Low
            "skills_match": 95,       # High
            "culture_fit": 90,        # High
            "recommendation_reason": "Good skills but low mission fit",
            "breakdown": {}
        })
        
        mock_generate.return_value = mock_response
        
        score = self.engine.score_application(self.application)
        
        # Should be "no" despite high skills and culture fit
        self.assertEqual(score.recommendation, "no")

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_score_caching(self, mock_generate):
        """Test that scores are cached."""
        mock_response = json.dumps({
            "mission_alignment": 80,
            "skills_match": 80,
            "culture_fit": 80,
            "recommendation_reason": "Good fit",
            "breakdown": {}
        })
        
        mock_generate.return_value = mock_response
        
        # First score
        score1 = self.engine.score_application(self.application)
        call_count_1 = mock_generate.call_count
        
        # Second score (should not call LLM)
        score2 = self.engine.score_application(self.application)
        call_count_2 = mock_generate.call_count
        
        self.assertEqual(call_count_1, call_count_2)
        self.assertEqual(score1.id, score2.id)

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_invalid_scoring_response(self, mock_generate):
        """Test handling of invalid scoring response."""
        mock_generate.return_value = "Invalid JSON"
        
        with self.assertRaises(ScoringError):
            self.engine.score_application(self.application)

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_bulk_score_applications(self, mock_generate):
        """Test bulk scoring multiple applications."""
        mock_response = json.dumps({
            "mission_alignment": 80,
            "skills_match": 80,
            "culture_fit": 80,
            "recommendation_reason": "Good fit",
            "breakdown": {}
        })
        
        mock_generate.return_value = mock_response
        
        # Create multiple applications
        applicants = [
            User.objects.create_user(f"app{i}@test.com", "pass")
            for i in range(3)
        ]
        
        apps = [
            Application.objects.create(job=self.job, applicant=app)
            for app in applicants
        ]
        
        scores = self.engine.bulk_score_applications(apps)
        
        self.assertEqual(len(scores), 3)
        self.assertEqual(ApplicationScore.objects.filter(application__job=self.job).count(), 3)

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_get_shortlist(self, mock_generate):
        """Test retrieving qualified shortlist."""
        mock_response = json.dumps({
            "mission_alignment": 80,
            "skills_match": 80,
            "culture_fit": 80,
            "recommendation_reason": "Good",
            "breakdown": {}
        })
        
        mock_generate.return_value = mock_response
        
        # Score the application
        self.engine.score_application(self.application)
        
        shortlist = self.engine.get_shortlist(self.job, min_score=Decimal("70"))
        
        self.assertEqual(len(shortlist), 1)
        self.assertEqual(shortlist[0].application.id, self.application.id)

    @patch('jobs.services.scoring_engine.AIClient.generate')
    def test_get_shortlist_filters_by_min_score(self, mock_generate):
        """Test shortlist respects minimum score threshold."""
        # Score 1: 50 (below threshold)
        # Score 2: 85 (above threshold)
        
        def score_response(self, prompt, max_tokens=1500):
            if "app1" in prompt:
                return json.dumps({
                    "mission_alignment": 40,
                    "skills_match": 50,
                    "culture_fit": 60,
                    "recommendation_reason": "Below threshold",
                    "breakdown": {}
                })
            else:
                return json.dumps({
                    "mission_alignment": 85,
                    "skills_match": 85,
                    "culture_fit": 85,
                    "recommendation_reason": "Above threshold",
                    "breakdown": {}
                })
        
        with patch.object(self.engine.ai_client, 'generate', side_effect=score_response):
            app1 = Application.objects.create(
                job=self.job,
                applicant=User.objects.create_user("app1@test.com", "pass")
            )
            app2 = Application.objects.create(
                job=self.job,
                applicant=User.objects.create_user("app2@test.com", "pass")
            )
            
            self.engine.score_application(app1)
            self.engine.score_application(app2)
            
            shortlist = self.engine.get_shortlist(self.job, min_score=Decimal("75"))
            
            # Only app2 should be in shortlist
            self.assertEqual(len(shortlist), 1)
            self.assertEqual(shortlist[0].application.id, app2.id)


class HireForMissionAPITestCase(TestCase):
    """Test suite for Hire for Mission API endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = Client()
        
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.category = Category.objects.create(
            name="Tech",
            slug="tech",
        )
        
        self.job = Job.objects.create(
            title="Engineer",
            slug="engineer",
            organization=self.org,
            category=self.category,
            description="Build things",
            requirements="Tech skills",
        )
        
        # Create hiring team member
        self.hiring_user = User.objects.create_user(
            email="hiring@test.com",
            password="testpass123",
        )
        self.org.members.add(self.hiring_user)
        
        # Create applicant
        self.applicant = User.objects.create_user(
            email="applicant@test.com",
            password="testpass123",
        )
        
        self.application = Application.objects.create(
            job=self.job,
            applicant=self.applicant,
        )

    @patch('jobs.views.hire_for_mission.MissionQuestionGenerator.generate_questions_for_job')
    def test_generate_questions_endpoint(self, mock_generate):
        """Test /api/jobs/{job_id}/generate-screening-questions/ endpoint."""
        mock_questions = [
            MagicMock(
                id=1,
                question_type='mission_alignment',
                question_text="Why our mission?",
                get_question_type_display=lambda: "Mission Alignment",
                created_at="2024-01-01T00:00:00Z"
            )
        ]
        mock_generate.return_value = mock_questions
        
        # Login as hiring team member
        self.client.login(email="hiring@test.com", password="testpass123")
        
        response = self.client.post(
            reverse("jobs:api_generate_questions", kwargs={"job_id": self.job.id}),
            {"num_questions": "1"}
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertEqual(data["questions_generated"], 1)

    def test_generate_questions_unauthorized(self):
        """Test that non-team members cannot generate questions."""
        other_user = User.objects.create_user(
            email="other@test.com",
            password="testpass123",
        )
        
        self.client.login(email="other@test.com", password="testpass123")
        
        response = self.client.post(
            reverse("jobs:api_generate_questions", kwargs={"job_id": self.job.id}),
            {"num_questions": "1"}
        )
        
        self.assertEqual(response.status_code, 403)

    def test_submit_application_endpoint(self):
        """Test POST /api/applications/ endpoint."""
        self.client.login(email="applicant@test.com", password="testpass123")
        
        response = self.client.post(
            reverse("jobs:api_submit_application"),
            {
                "job_id": str(self.job.id),
                "cover_letter": "I'm interested",
                "responses": json.dumps({})
            }
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])

    @patch('jobs.views.hire_for_mission.CandidateScoringEngine.score_application')
    def test_score_application_endpoint(self, mock_score):
        """Test POST /api/applications/{app_id}/score/ endpoint."""
        mock_score_obj = MagicMock()
        mock_score_obj.overall_score = Decimal("85.50")
        mock_score_obj.mission_alignment_score = Decimal("85")
        mock_score_obj.skills_match_score = Decimal("85")
        mock_score_obj.culture_fit_score = Decimal("85")
        mock_score_obj.recommendation = "strong_yes"
        mock_score_obj.get_recommendation_display = lambda: "Strong Yes - Highly Recommended"
        mock_score_obj.recommendation_reason = "Great fit"
        mock_score.return_value = mock_score_obj
        
        self.client.login(email="hiring@test.com", password="testpass123")
        
        response = self.client.post(
            reverse("jobs:api_score_application", kwargs={"app_id": self.application.id})
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertEqual(data["scores"]["overall"], 85.5)

    @patch('jobs.views.hire_for_mission.CandidateScoringEngine.get_shortlist')
    def test_shortlist_endpoint(self, mock_shortlist):
        """Test GET /api/jobs/{job_id}/shortlist/ endpoint."""
        mock_score = MagicMock()
        mock_score.overall_score = Decimal("85")
        mock_score.mission_alignment_score = Decimal("85")
        mock_score.skills_match_score = Decimal("85")
        mock_score.culture_fit_score = Decimal("85")
        mock_score.recommendation = "strong_yes"
        mock_score.get_recommendation_display = lambda: "Strong Yes"
        mock_score.application = self.application
        
        self.application.cover_letter = "Cover"
        
        mock_shortlist.return_value = [mock_score]
        
        self.client.login(email="hiring@test.com", password="testpass123")
        
        response = self.client.get(
            reverse("jobs:api_job_shortlist", kwargs={"job_id": self.job.id})
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertEqual(len(data["candidates"]), 1)

    def test_hiring_feedback_endpoint(self):
        """Test POST /api/applications/{app_id}/feedback/ endpoint."""
        self.client.login(email="hiring@test.com", password="testpass123")
        
        response = self.client.post(
            reverse("jobs:api_submit_feedback", kwargs={"app_id": self.application.id}),
            {
                "ai_score_helpful": "true",
                "score_accuracy": "accurate",
                "hiring_decision": "interview",
                "notes": "Good candidate"
            }
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        
        # Verify feedback was saved
        feedback = HiringFeedback.objects.get(application=self.application)
        self.assertTrue(feedback.ai_score_helpful)
        self.assertEqual(feedback.score_accuracy, "accurate")


class ModelTestCase(TestCase):
    """Test Hire for Mission models."""

    def setUp(self):
        """Set up test fixtures."""
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
        )
        
        self.category = Category.objects.create(
            name="Health",
            slug="health",
        )
        
        self.job = Job.objects.create(
            title="Doctor",
            slug="doctor",
            organization=self.org,
            category=self.category,
            description="Treat patients",
            requirements="Medical degree",
        )
        
        self.applicant = User.objects.create_user(
            email="applicant@test.com",
        )
        
        self.application = Application.objects.create(
            job=self.job,
            applicant=self.applicant,
        )

    def test_mission_screening_question_creation(self):
        """Test creating screening questions."""
        question = MissionScreeningQuestion.objects.create(
            job=self.job,
            question_type="mission_alignment",
            question_text="Why do you want to save lives?",
        )
        
        self.assertEqual(str(question), f"{self.job.title} - Mission Alignment")
        self.assertTrue(question.is_active)

    def test_job_application_response_creation(self):
        """Test creating application responses."""
        question = MissionScreeningQuestion.objects.create(
            job=self.job,
            question_type="mission_alignment",
            question_text="Why?",
        )
        
        response = JobApplicationResponse.objects.create(
            application=self.application,
            question=question,
            response_text="Because I care",
        )
        
        self.assertEqual(
            str(response),
            f"{self.application.applicant} - Mission Alignment"
        )

    def test_application_score_creation(self):
        """Test creating application scores."""
        score = ApplicationScore.objects.create(
            application=self.application,
            overall_score=Decimal("85.50"),
            mission_alignment_score=Decimal("85"),
            skills_match_score=Decimal("85"),
            culture_fit_score=Decimal("85"),
            recommendation="strong_yes",
        )
        
        self.assertEqual(
            str(score),
            f"{self.application.applicant} - Score: 85.50"
        )

    def test_hiring_feedback_creation(self):
        """Test creating hiring feedback."""
        reviewer = User.objects.create_user(email="reviewer@test.com")
        
        feedback = HiringFeedback.objects.create(
            application=self.application,
            reviewer=reviewer,
            ai_score_helpful=True,
            score_accuracy="accurate",
            hiring_decision="interview",
        )
        
        self.assertTrue(feedback.ai_score_helpful)
        self.assertEqual(feedback.hiring_decision, "interview")
