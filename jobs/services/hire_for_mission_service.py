"""
Service layer for Hire for Mission business logic.
Handles scoring, team consensus, and candidate management.
"""
from django.db.models import Avg, Q, Count
from django.utils import timezone
from typing import Dict, List, Optional

from jobs.models import (
    ScreeningSession,
    ScreeningCandidate,
    TeamFeedback,
    Application,
)


class ScreeningService:
    """Service for managing screening sessions."""
    
    @staticmethod
    def create_screening_session(job, weights: Dict[str, int] = None) -> ScreeningSession:
        """Create a new screening session for a job."""
        if weights is None:
            weights = {
                'skills': 40,
                'mission': 35,
                'culture': 25,
            }
        
        return ScreeningSession.objects.create(
            job=job,
            status=ScreeningSession.Status.DRAFT,
            skills_weight=weights.get('skills', 40),
            mission_weight=weights.get('mission', 35),
            culture_weight=weights.get('culture', 25),
        )
    
    @staticmethod
    def add_candidate_to_session(session: ScreeningSession, application: Application) -> ScreeningCandidate:
        """Add an applicant to a screening session."""
        candidate, created = ScreeningCandidate.objects.get_or_create(
            session=session,
            application=application,
        )
        return candidate
    
    @staticmethod
    def bulk_add_candidates(session: ScreeningSession) -> int:
        """Add all applicants for the job to the screening session."""
        applications = Application.objects.filter(
            job=session.job,
        ).exclude(
            screening_candidate__session=session
        )
        
        count = 0
        for app in applications:
            ScreeningService.add_candidate_to_session(session, app)
            count += 1
        
        return count


class ScoringService:
    """Service for handling candidate scoring."""
    
    @staticmethod
    def score_candidate(
        candidate: ScreeningCandidate,
        skills_score: int,
        mission_score: int,
        culture_score: int,
        reasoning: str = "",
    ) -> None:
        """Update candidate scores."""
        candidate.skills_score = max(0, min(100, skills_score))
        candidate.mission_score = max(0, min(100, mission_score))
        candidate.culture_score = max(0, min(100, culture_score))
        candidate.reasoning = reasoning
        candidate.combined_score = candidate.calculate_combined_score()
        candidate.scored_at = timezone.now()
        candidate.save()
    
    @staticmethod
    def flag_concerns(
        candidate: ScreeningCandidate,
        concerns_text: str,
    ) -> None:
        """Flag a candidate with concerns."""
        candidate.has_concerns = True
        candidate.concerns_text = concerns_text
        candidate.save()
    
    @staticmethod
    def clear_concerns(candidate: ScreeningCandidate) -> None:
        """Clear concerns flag from a candidate."""
        candidate.has_concerns = False
        candidate.concerns_text = ""
        candidate.save()
    
    @staticmethod
    def get_score_distribution(session: ScreeningSession) -> Dict[str, int]:
        """Get score distribution for analytics."""
        candidates = session.candidates.all()
        
        return {
            "0-20": candidates.filter(combined_score__lt=20).count(),
            "20-40": candidates.filter(combined_score__gte=20, combined_score__lt=40).count(),
            "40-60": candidates.filter(combined_score__gte=40, combined_score__lt=60).count(),
            "60-80": candidates.filter(combined_score__gte=60, combined_score__lt=80).count(),
            "80-100": candidates.filter(combined_score__gte=80).count(),
        }


class FeedbackService:
    """Service for managing team feedback."""
    
    @staticmethod
    def submit_feedback(
        candidate: ScreeningCandidate,
        reviewer,
        rating: int,
        comment: str = "",
        recommendation: str = ScreeningCandidate.Recommendation.PENDING,
    ) -> TeamFeedback:
        """Submit feedback for a candidate."""
        feedback, created = TeamFeedback.objects.update_or_create(
            candidate=candidate,
            reviewer=reviewer,
            defaults={
                'rating': max(1, min(5, rating)),
                'comment': comment,
                'recommendation': recommendation,
            }
        )
        
        # Update candidate's team consensus
        FeedbackService.update_team_consensus(candidate)
        
        return feedback
    
    @staticmethod
    def update_team_consensus(candidate: ScreeningCandidate) -> None:
        """Update team consensus score and recommendation."""
        feedback = candidate.team_feedback.all()
        
        # Calculate average rating
        avg_rating = feedback.aggregate(Avg('rating'))['rating__avg']
        candidate.team_consensus_score = avg_rating or 0.0
        
        # Determine consensus recommendation
        if feedback.exists():
            recommendations = feedback.values_list('recommendation', flat=True)
            
            # Simple consensus: most common recommendation
            from collections import Counter
            counter = Counter(recommendations)
            if counter:
                most_common = counter.most_common(1)[0][0]
                candidate.team_recommendation = most_common
        
        candidate.save()
    
    @staticmethod
    def get_team_consensus_summary(session: ScreeningSession) -> Dict[str, int]:
        """Get team consensus summary for analytics."""
        feedback = TeamFeedback.objects.filter(
            candidate__session=session
        )
        
        return {
            'strong_yes': feedback.filter(recommendation='strong_yes').count(),
            'yes': feedback.filter(recommendation='yes').count(),
            'maybe': feedback.filter(recommendation='maybe').count(),
            'no': feedback.filter(recommendation='no').count(),
            'strong_no': feedback.filter(recommendation='strong_no').count(),
            'pending': feedback.filter(recommendation='pending').count(),
        }


class AnalyticsService:
    """Service for screening analytics."""
    
    @staticmethod
    def get_analytics(session: ScreeningSession) -> Dict:
        """Get comprehensive analytics for a screening session."""
        candidates = session.candidates.all()
        
        total_applicants = candidates.count()
        qualified_count = candidates.filter(combined_score__gte=70).count()
        qualified_pct = int((qualified_count / total_applicants * 100) if total_applicants > 0 else 0)
        
        avg_score = candidates.aggregate(Avg('combined_score'))['combined_score__avg'] or 0
        
        # Hours saved (assuming 30 min per application review)
        hours_saved = (total_applicants * 30) / 60
        
        # Cost per hire (baseline: $2000 cost per hire)
        cost_per_hire = int(2000 / (qualified_count if qualified_count > 0 else 1))
        
        return {
            'total_applications': total_applicants,
            'qualified_count': qualified_count,
            'qualified_percentage': qualified_pct,
            'average_score': int(avg_score),
            'hours_saved': int(hours_saved),
            'cost_per_hire': cost_per_hire,
            'roi_value': int(hours_saved * 100),  # $100/hour assumed value
        }
    
    @staticmethod
    def get_top_candidates(session: ScreeningSession, limit: int = 5) -> List[ScreeningCandidate]:
        """Get top candidates by combined score."""
        return session.candidates.select_related(
            'application__applicant'
        ).annotate(
            avg_rating=Avg('team_feedback__rating')
        ).order_by('-combined_score')[:limit]
    
    @staticmethod
    def get_candidate_stats(session: ScreeningSession) -> Dict:
        """Get detailed candidate statistics."""
        candidates = session.candidates.all()
        feedback = TeamFeedback.objects.filter(candidate__session=session)
        
        return {
            'total_candidates': candidates.count(),
            'with_feedback': candidates.filter(team_feedback__isnull=False).distinct().count(),
            'with_concerns': candidates.filter(has_concerns=True).count(),
            'reviewed_by_team': feedback.values('reviewer').distinct().count(),
            'average_team_rating': feedback.aggregate(Avg('rating'))['rating__avg'] or 0,
        }


class ShortlistService:
    """Service for managing shortlist."""
    
    @staticmethod
    def get_shortlist(
        session: ScreeningSession,
        limit: int = 20,
        min_score: int = None,
        max_score: int = None,
        has_concerns: bool = None,
        team_recommendation: str = None,
    ) -> List[ScreeningCandidate]:
        """Get shortlisted candidates with optional filters."""
        candidates = session.candidates.select_related(
            'application__applicant'
        ).annotate(
            avg_rating=Avg('team_feedback__rating')
        )
        
        # Apply filters
        if min_score is not None:
            candidates = candidates.filter(combined_score__gte=min_score)
        
        if max_score is not None:
            candidates = candidates.filter(combined_score__lte=max_score)
        
        if has_concerns is not None:
            candidates = candidates.filter(has_concerns=has_concerns)
        
        if team_recommendation is not None:
            candidates = candidates.filter(team_recommendation=team_recommendation)
        
        # Sort by combined score
        return candidates.order_by('-combined_score')[:limit]
    
    @staticmethod
    def update_ranking(session: ScreeningSession) -> None:
        """Update ranked positions for all candidates."""
        candidates = session.candidates.order_by('-combined_score')
        for position, candidate in enumerate(candidates, 1):
            if position <= 20:
                candidate.ranked_position = position
                candidate.save(update_fields=['ranked_position'])


class ActionService:
    """Service for candidate actions."""
    
    @staticmethod
    def invite_candidate(candidate: ScreeningCandidate) -> None:
        """Mark candidate as invited."""
        candidate.team_recommendation = ScreeningCandidate.Recommendation.STRONG_YES
        candidate.save()
    
    @staticmethod
    def reject_candidate(candidate: ScreeningCandidate) -> None:
        """Mark candidate as rejected."""
        candidate.team_recommendation = ScreeningCandidate.Recommendation.STRONG_NO
        candidate.save()
    
    @staticmethod
    def move_to_review(candidate: ScreeningCandidate) -> None:
        """Mark candidate as pending review."""
        candidate.team_recommendation = ScreeningCandidate.Recommendation.MAYBE
        candidate.save()


# Example usage
if __name__ == "__main__":
    """
    Example of how to use the service layer:
    
    # Create a screening session
    from jobs.models import Job
    job = Job.objects.get(id=1)
    session = ScreeningService.create_screening_session(
        job=job,
        weights={'skills': 40, 'mission': 35, 'culture': 25}
    )
    
    # Add candidates
    ScreeningService.bulk_add_candidates(session)
    
    # Score a candidate
    candidate = session.candidates.first()
    ScoringService.score_candidate(
        candidate=candidate,
        skills_score=85,
        mission_score=90,
        culture_score=80,
        reasoning="Strong technical background with alignment to mission."
    )
    
    # Get analytics
    analytics = AnalyticsService.get_analytics(session)
    print(f"Qualified: {analytics['qualified_percentage']}%")
    
    # Get shortlist
    shortlist = ShortlistService.get_shortlist(
        session=session,
        min_score=70
    )
    """
    pass
