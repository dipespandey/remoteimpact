"""
API views for Hire for Mission screening and evaluation features.
Handles question generation, application submission, scoring, and shortlist retrieval.
"""

import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Avg
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from jobs.models import (
    Application,
    ApplicationScore,
    HiringFeedback,
    Job,
    JobApplicationResponse,
    MissionScreeningQuestion,
)
from jobs.services.question_generator import MissionQuestionGenerator, QuestionGenerationError
from jobs.services.scoring_engine import CandidateScoringEngine, ScoringError

logger = logging.getLogger(__name__)


# ============================================================================
# QUESTION GENERATION ENDPOINT
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["POST"]), name="dispatch")
class GenerateScreeningQuestionsView(View):
    """
    POST /api/jobs/{job_id}/generate-screening-questions/
    
    Generate AI screening questions for a job based on org mission and role.
    Only hiring team members can trigger this.
    """

    def post(self, request, job_id):
        """Generate screening questions for a job."""
        try:
            job = get_object_or_404(Job, id=job_id, is_active=True)
            
            # Check permissions - must be org member or staff
            if not self._has_permission(request.user, job):
                return JsonResponse(
                    {"error": "Not authorized to generate questions for this job"},
                    status=403
                )
            
            # Get parameters
            num_questions = int(request.POST.get("num_questions", 4))
            force_regenerate = request.POST.get("force_regenerate") == "true"
            
            if num_questions < 1 or num_questions > 10:
                return JsonResponse(
                    {"error": "num_questions must be between 1 and 10"},
                    status=400
                )
            
            # Generate questions
            generator = MissionQuestionGenerator()
            questions = generator.generate_questions_for_job(
                job=job,
                num_questions=num_questions,
                force_regenerate=force_regenerate,
            )
            
            return JsonResponse({
                "success": True,
                "job_id": job.id,
                "job_title": job.title,
                "questions_generated": len(questions),
                "questions": [
                    {
                        "id": q.id,
                        "type": q.get_question_type_display(),
                        "text": q.question_text,
                        "created_at": q.created_at.isoformat(),
                    }
                    for q in questions
                ]
            })
            
        except QuestionGenerationError as e:
            logger.error(f"Question generation error: {e}")
            return JsonResponse(
                {"error": str(e)},
                status=400
            )
        except Exception as e:
            logger.error(f"Unexpected error in question generation: {e}")
            return JsonResponse(
                {"error": "Failed to generate questions"},
                status=500
            )

    def _has_permission(self, user, job) -> bool:
        """Check if user can generate questions for this job."""
        if user.is_staff:
            return True
        return job.organization.members.filter(id=user.id).exists()


# ============================================================================
# APPLICATION SUBMISSION WITH SCREENING RESPONSES
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["POST"]), name="dispatch")
class SubmitApplicationWithResponsesView(View):
    """
    POST /api/applications/
    
    Submit a job application with screening question responses.
    """

    def post(self, request):
        """Submit application with screening responses."""
        try:
            job_id = request.POST.get("job_id")
            cover_letter = request.POST.get("cover_letter", "")
            responses_json = request.POST.get("responses", "{}")  # {"question_id": "response text"}
            
            if not job_id:
                return JsonResponse({"error": "job_id is required"}, status=400)
            
            job = get_object_or_404(Job, id=job_id, is_active=True)
            
            # Create or get application
            application, created = Application.objects.update_or_create(
                job=job,
                applicant=request.user,
                defaults={"cover_letter": cover_letter}
            )
            
            # Save screening responses
            import json
            try:
                responses_data = json.loads(responses_json)
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid responses JSON"}, status=400)
            
            for question_id, response_text in responses_data.items():
                try:
                    question = MissionScreeningQuestion.objects.get(id=question_id)
                    JobApplicationResponse.objects.update_or_create(
                        application=application,
                        question=question,
                        defaults={"response_text": response_text}
                    )
                except MissionScreeningQuestion.DoesNotExist:
                    logger.warning(f"Question {question_id} not found")
                    continue
            
            return JsonResponse({
                "success": True,
                "application_id": application.id,
                "job_id": job.id,
                "applicant_email": request.user.email,
                "message": "Application submitted successfully"
            })
            
        except Exception as e:
            logger.error(f"Error submitting application: {e}")
            return JsonResponse(
                {"error": "Failed to submit application"},
                status=500
            )


# ============================================================================
# APPLICATION SCORING
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["POST"]), name="dispatch")
class ScoreApplicationView(View):
    """
    POST /api/applications/{app_id}/score/
    
    Trigger AI scoring for an application.
    Only hiring team can access.
    """

    def post(self, request, app_id):
        """Score an application."""
        try:
            application = get_object_or_404(Application, id=app_id)
            
            # Check permission
            if not self._has_permission(request.user, application.job):
                return JsonResponse(
                    {"error": "Not authorized"},
                    status=403
                )
            
            force_rescore = request.POST.get("force_rescore") == "true"
            
            # Score the application
            engine = CandidateScoringEngine()
            score = engine.score_application(
                application=application,
                force_rescore=force_rescore,
            )
            
            return JsonResponse({
                "success": True,
                "application_id": application.id,
                "applicant": application.applicant.email,
                "job": application.job.title,
                "scores": {
                    "overall": float(score.overall_score),
                    "mission_alignment": float(score.mission_alignment_score),
                    "skills_match": float(score.skills_match_score),
                    "culture_fit": float(score.culture_fit_score),
                },
                "recommendation": score.get_recommendation_display(),
                "reason": score.recommendation_reason,
            })
            
        except ScoringError as e:
            logger.error(f"Scoring error: {e}")
            return JsonResponse(
                {"error": str(e)},
                status=400
            )
        except Exception as e:
            logger.error(f"Unexpected error in scoring: {e}")
            return JsonResponse(
                {"error": "Failed to score application"},
                status=500
            )

    def _has_permission(self, user, job) -> bool:
        """Check if user can score applications for this job."""
        if user.is_staff:
            return True
        return job.organization.members.filter(id=user.id).exists()


# ============================================================================
# SHORTLIST RETRIEVAL
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["GET"]), name="dispatch")
class JobShortlistView(View):
    """
    GET /api/jobs/{job_id}/shortlist/
    
    Get top-scored candidates for a job.
    Only hiring team can access.
    """

    def get(self, request, job_id):
        """Get shortlist for a job."""
        try:
            job = get_object_or_404(Job, id=job_id, is_active=True)
            
            # Check permission
            if not self._has_permission(request.user, job):
                return JsonResponse(
                    {"error": "Not authorized"},
                    status=403
                )
            
            # Get parameters
            min_score = Decimal(request.GET.get("min_score", "70"))
            limit = int(request.GET.get("limit", "20"))
            
            if limit > 100:
                limit = 100
            
            # Get shortlist
            engine = CandidateScoringEngine()
            shortlist = engine.get_shortlist(
                job=job,
                min_score=min_score,
                max_results=limit,
            )
            
            # Format response
            candidates = []
            for score in shortlist:
                app = score.application
                candidates.append({
                    "application_id": app.id,
                    "applicant": app.applicant.get_full_name() or app.applicant.email,
                    "email": app.applicant.email,
                    "applied_at": app.applied_at.isoformat(),
                    "scores": {
                        "overall": float(score.overall_score),
                        "mission_alignment": float(score.mission_alignment_score),
                        "skills_match": float(score.skills_match_score),
                        "culture_fit": float(score.culture_fit_score),
                    },
                    "recommendation": score.get_recommendation_display(),
                    "cover_letter_excerpt": app.cover_letter[:200] if app.cover_letter else "",
                })
            
            return JsonResponse({
                "success": True,
                "job_id": job.id,
                "job_title": job.title,
                "total_candidates": candidates.__len__(),
                "min_score_threshold": float(min_score),
                "candidates": candidates,
            })
            
        except Exception as e:
            logger.error(f"Error retrieving shortlist: {e}")
            return JsonResponse(
                {"error": "Failed to retrieve shortlist"},
                status=500
            )

    def _has_permission(self, user, job) -> bool:
        """Check if user can view shortlist for this job."""
        if user.is_staff:
            return True
        return job.organization.members.filter(id=user.id).exists()


# ============================================================================
# JOB ANALYTICS
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["GET"]), name="dispatch")
class JobAnalyticsView(View):
    """
    GET /api/jobs/{job_id}/analytics/
    
    Get analytics on applications and screening metrics.
    """

    def get(self, request, job_id):
        """Get job analytics."""
        try:
            job = get_object_or_404(Job, id=job_id, is_active=True)
            
            # Check permission
            if not self._has_permission(request.user, job):
                return JsonResponse(
                    {"error": "Not authorized"},
                    status=403
                )
            
            # Calculate metrics
            all_apps = Application.objects.filter(job=job)
            scored_apps = ApplicationScore.objects.filter(application__job=job)
            
            total_applications = all_apps.count()
            applications_scored = scored_apps.count()
            
            # Score distribution
            score_ranges = {
                "90-100": scored_apps.filter(overall_score__gte=90).count(),
                "80-89": scored_apps.filter(overall_score__gte=80, overall_score__lt=90).count(),
                "70-79": scored_apps.filter(overall_score__gte=70, overall_score__lt=80).count(),
                "60-69": scored_apps.filter(overall_score__gte=60, overall_score__lt=70).count(),
                "<60": scored_apps.filter(overall_score__lt=60).count(),
            }
            
            # Recommendations
            recommendations = {
                "strong_yes": scored_apps.filter(recommendation="strong_yes").count(),
                "yes": scored_apps.filter(recommendation="yes").count(),
                "maybe": scored_apps.filter(recommendation="maybe").count(),
                "no": scored_apps.filter(recommendation="no").count(),
            }
            
            # Average scores
            avg_scores = {
                "mission_alignment": float(
                    scored_apps.aggregate(avg=Avg("mission_alignment_score"))["avg"] or 0
                ),
                "skills_match": float(
                    scored_apps.aggregate(avg=Avg("skills_match_score"))["avg"] or 0
                ),
                "culture_fit": float(
                    scored_apps.aggregate(avg=Avg("culture_fit_score"))["avg"] or 0
                ),
            }
            
            return JsonResponse({
                "success": True,
                "job_id": job.id,
                "job_title": job.title,
                "applications": {
                    "total": total_applications,
                    "scored": applications_scored,
                    "unscored": total_applications - applications_scored,
                },
                "score_distribution": score_ranges,
                "recommendations": recommendations,
                "average_scores": avg_scores,
            })
            
        except Exception as e:
            logger.error(f"Error retrieving analytics: {e}")
            return JsonResponse(
                {"error": "Failed to retrieve analytics"},
                status=500
            )

    def _has_permission(self, user, job) -> bool:
        """Check if user can view analytics for this job."""
        if user.is_staff:
            return True
        return job.organization.members.filter(id=user.id).exists()


# ============================================================================
# HIRING FEEDBACK SUBMISSION
# ============================================================================


@method_decorator(login_required, name="dispatch")
@method_decorator(require_http_methods(["POST"]), name="dispatch")
class SubmitHiringFeedbackView(View):
    """
    POST /api/applications/{app_id}/feedback/
    
    Submit hiring team feedback on a scored application.
    Used to improve scoring algorithm.
    """

    def post(self, request, app_id):
        """Submit hiring feedback."""
        try:
            application = get_object_or_404(Application, id=app_id)
            
            # Check permission
            if not self._has_permission(request.user, application.job):
                return JsonResponse(
                    {"error": "Not authorized"},
                    status=403
                )
            
            # Get feedback data
            ai_score_helpful = request.POST.get("ai_score_helpful") == "true"
            score_accuracy = request.POST.get("score_accuracy", "")
            hiring_decision = request.POST.get("hiring_decision", "")
            notes = request.POST.get("notes", "")
            
            # Validate
            valid_decisions = [c[0] for c in Application.Status.choices]
            if hiring_decision and hiring_decision not in valid_decisions:
                return JsonResponse(
                    {"error": f"Invalid hiring_decision: {hiring_decision}"},
                    status=400
                )
            
            # Create feedback
            feedback, created = HiringFeedback.objects.update_or_create(
                application=application,
                defaults={
                    "reviewer": request.user,
                    "ai_score_helpful": ai_score_helpful,
                    "score_accuracy": score_accuracy,
                    "hiring_decision": hiring_decision,
                    "notes": notes,
                }
            )
            
            return JsonResponse({
                "success": True,
                "application_id": application.id,
                "feedback_id": feedback.id,
                "message": "Feedback submitted successfully"
            })
            
        except Exception as e:
            logger.error(f"Error submitting feedback: {e}")
            return JsonResponse(
                {"error": "Failed to submit feedback"},
                status=500
            )

    def _has_permission(self, user, job) -> bool:
        """Check if user can submit feedback for this job."""
        if user.is_staff:
            return True
        return job.organization.members.filter(id=user.id).exists()


# ============================================================================
# TEMPLATE VIEWS (for HTML dashboard UI)
# ============================================================================


@method_decorator(login_required, name="dispatch")
class HireForMissionDashboardView(TemplateView):
    """Main dashboard for Hire for Mission feature."""
    template_name = "jobs/hire_for_mission/dashboard.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Get user's organizations
        if user.is_staff:
            jobs = Job.objects.filter(is_active=True)
        else:
            jobs = Job.objects.filter(
                organization__members=user,
                is_active=True
            )
        
        context['jobs'] = jobs
        context['total_jobs'] = jobs.count()
        
        # Get total applications
        context['total_applications'] = Application.objects.filter(
            job__in=jobs
        ).count()
        
        # Get scored applications
        context['scored_applications'] = ApplicationScore.objects.filter(
            application__job__in=jobs
        ).count()
        
        return context


@method_decorator(login_required, name="dispatch")
class ShortlistView(TemplateView):
    """View for shortlist of top candidates."""
    template_name = "jobs/hire_for_mission/shortlist.html"
    
    def get_context_data(self, session_id=None, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Get job (using session_id as proxy for job_id in this simplified version)
        try:
            job = Job.objects.get(id=session_id, is_active=True)
        except Job.DoesNotExist:
            context['error'] = "Job not found"
            return context
        
        # Check permission
        if not user.is_staff and not job.organization.members.filter(id=user.id).exists():
            context['error'] = "Not authorized"
            return context
        
        # Get applications with scores, sorted by combined_score descending
        applications = Application.objects.filter(
            job=job
        ).select_related('applicant').prefetch_related('ai_score')
        
        # Add scores to context
        shortlist = []
        for app in applications[:20]:  # Top 20
            score_obj = getattr(app, 'ai_score', None)
            shortlist.append({
                'id': app.id,
                'applicant_name': app.applicant.name if app.applicant else 'Unknown',
                'email': app.applicant.email if app.applicant else '',
                'combined_score': float(score_obj.combined_score) if score_obj else 0,
                'skills_score': float(score_obj.skills_score) if score_obj else 0,
                'mission_alignment_score': float(score_obj.mission_alignment_score) if score_obj else 0,
                'culture_fit_score': float(score_obj.culture_fit_score) if score_obj else 0,
            })
        
        context['job'] = job
        context['shortlist'] = shortlist
        context['total_applications'] = applications.count()
        
        return context


@method_decorator(login_required, name="dispatch")
class CandidateDetailView(TemplateView):
    """Detailed view of a single candidate."""
    template_name = "jobs/hire_for_mission/candidate_detail.html"
    
    def get_context_data(self, candidate_id=None, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        try:
            application = Application.objects.select_related(
                'job', 'applicant', 'ai_score'
            ).get(id=candidate_id)
        except Application.DoesNotExist:
            context['error'] = "Candidate not found"
            return context
        
        # Check permission
        if not user.is_staff and not application.job.organization.members.filter(id=user.id).exists():
            context['error'] = "Not authorized"
            return context
        
        # Get screening responses
        responses = JobApplicationResponse.objects.filter(
            application=application
        ).select_related('question')
        
        # Get team feedback
        feedback = HiringFeedback.objects.filter(
            application=application
        ).select_related('feedback_from_user')
        
        # Prepare context
        context['application'] = application
        context['job'] = application.job
        context['responses'] = responses
        context['feedback'] = feedback
        context['ai_score'] = getattr(application, 'ai_score', None)
        
        return context


@method_decorator(login_required, name="dispatch")
class AnalyticsDashboardView(TemplateView):
    """Analytics dashboard for hiring metrics."""
    template_name = "jobs/hire_for_mission/analytics.html"
    
    def get_context_data(self, session_id=None, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        try:
            job = Job.objects.get(id=session_id, is_active=True)
        except Job.DoesNotExist:
            context['error'] = "Job not found"
            return context
        
        # Check permission
        if not user.is_staff and not job.organization.members.filter(id=user.id).exists():
            context['error'] = "Not authorized"
            return context
        
        # Get applications
        applications = Application.objects.filter(job=job)
        total_apps = applications.count()
        
        # Get scores
        scores = ApplicationScore.objects.filter(
            application__job=job
        )
        
        # Calculate metrics
        qualified_count = scores.filter(combined_score__gte=70).count()
        avg_score = scores.aggregate(avg=Avg('combined_score'))['avg'] or 0
        
        context['job'] = job
        context['total_applications'] = total_apps
        context['qualified_count'] = qualified_count
        context['qualified_percentage'] = (qualified_count / total_apps * 100) if total_apps > 0 else 0
        context['avg_score'] = float(avg_score)
        context['time_saved_hours'] = (total_apps * 0.03) / 60  # ~1.8 min per app
        context['cost_saved'] = total_apps * 1.20  # ~$1.20 per app vs manual
        
        return context


@method_decorator(login_required, name="dispatch")
class CandidateListAPIView(View):
    """API endpoint for candidate list (JSON)."""
    
    def get(self, request, session_id):
        """Get candidates for a job as JSON."""
        user = request.user
        
        try:
            job = Job.objects.get(id=session_id, is_active=True)
        except Job.DoesNotExist:
            return JsonResponse({'error': 'Job not found'}, status=404)
        
        # Check permission
        if not user.is_staff and not job.organization.members.filter(id=user.id).exists():
            return JsonResponse({'error': 'Not authorized'}, status=403)
        
        # Get applications with pagination
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 50))
        
        applications = Application.objects.filter(
            job=job
        ).select_related('applicant', 'ai_score').order_by('-created_at')
        
        start = (page - 1) * per_page
        end = start + per_page
        
        candidates = []
        for app in applications[start:end]:
            score_obj = getattr(app, 'ai_score', None)
            candidates.append({
                'id': str(app.id),
                'name': app.applicant.name if app.applicant else 'Unknown',
                'email': app.applicant.email if app.applicant else '',
                'combined_score': float(score_obj.combined_score) if score_obj else None,
                'skills_score': float(score_obj.skills_score) if score_obj else None,
                'mission_alignment_score': float(score_obj.mission_alignment_score) if score_obj else None,
                'culture_fit_score': float(score_obj.culture_fit_score) if score_obj else None,
            })
        
        return JsonResponse({
            'total': applications.count(),
            'page': page,
            'per_page': per_page,
            'candidates': candidates,
        })
