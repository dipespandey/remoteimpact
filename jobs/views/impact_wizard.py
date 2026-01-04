"""
Impact Profile Wizard Views.

Multi-step onboarding wizard for building a seeker's Impact Profile.
Supports save/resume functionality with HTMX for smooth transitions.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import redirect, get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from ..models import SeekerProfile, Category
from ..constants.skills import SKILLS, SKILL_CATEGORIES, get_categorized_skill_choices


# Wizard step definitions
WIZARD_STEPS = [
    {"slug": "welcome", "title": "Welcome", "progress": 0},
    {"slug": "impact-areas", "title": "Impact Areas", "progress": 12},
    {"slug": "work-style", "title": "Work Style", "progress": 25},
    {"slug": "experience", "title": "Experience", "progress": 37},
    {"slug": "skills", "title": "Skills", "progress": 50},
    {"slug": "preferences", "title": "Preferences", "progress": 62},
    {"slug": "story", "title": "Your Story", "progress": 75},
    {"slug": "assessment", "title": "Quick Assessment", "progress": 87},
    {"slug": "summary", "title": "Summary", "progress": 100},
]


def get_or_create_seeker_profile(user):
    """Get or create a SeekerProfile for the user."""
    profile, created = SeekerProfile.objects.get_or_create(user=user)
    return profile


class ImpactWizardView(LoginRequiredMixin, TemplateView):
    """Main wizard view - handles full page load and resume."""

    template_name = "jobs/impact_wizard/wizard.html"

    def get(self, request, *args, **kwargs):
        profile = get_or_create_seeker_profile(request.user)

        # Allow editing with ?edit=true, otherwise redirect if completed
        edit_mode = request.GET.get("edit") == "true"
        if profile.wizard_completed and not edit_mode:
            return redirect("jobs:impact_profile")

        # In edit mode, start from beginning; otherwise resume from last step
        if edit_mode:
            step_index = 1  # Skip welcome, go straight to impact-areas
        else:
            step_index = min(profile.wizard_step, len(WIZARD_STEPS) - 1)

        current_step = WIZARD_STEPS[step_index]

        context = self.get_context_data(
            profile=profile,
            current_step=current_step,
            step_index=step_index,
            steps=WIZARD_STEPS,
            edit_mode=edit_mode,
        )
        return self.render_to_response(context)


class ImpactWizardStepView(LoginRequiredMixin, View):
    """Handles individual wizard step rendering and submission via HTMX."""

    def get(self, request, step_slug):
        """Render a specific step."""
        profile = get_or_create_seeker_profile(request.user)
        step_index = self._get_step_index(step_slug)

        if step_index is None:
            return JsonResponse({"error": "Invalid step"}, status=400)

        current_step = WIZARD_STEPS[step_index]
        context = self._get_step_context(profile, step_index, current_step)

        template = f"jobs/impact_wizard/steps/{step_slug.replace('-', '_')}.html"
        return TemplateResponse(request, template, context)

    def post(self, request, step_slug):
        """Process step submission and return next step."""
        profile = get_or_create_seeker_profile(request.user)
        step_index = self._get_step_index(step_slug)
        is_htmx = request.headers.get("HX-Request")

        if step_index is None:
            return JsonResponse({"error": "Invalid step"}, status=400)

        # Process the step data
        success, errors = self._process_step(request, profile, step_slug)

        if not success:
            # Re-render current step with errors
            current_step = WIZARD_STEPS[step_index]
            context = self._get_step_context(profile, step_index, current_step)
            context["errors"] = errors
            template = f"jobs/impact_wizard/steps/{step_slug.replace('-', '_')}.html"

            # For non-HTMX requests, render full page
            if not is_htmx:
                return redirect(
                    reverse("jobs:impact_wizard_step", kwargs={"step_slug": step_slug})
                )
            return TemplateResponse(request, template, context)

        # Move to next step
        next_index = step_index + 1

        if next_index >= len(WIZARD_STEPS):
            # Wizard complete
            profile.wizard_completed = True
            profile.wizard_step = len(WIZARD_STEPS) - 1
            profile.save()

            # Check if this is an HTMX request
            if is_htmx:
                response = TemplateResponse(
                    request,
                    "jobs/impact_wizard/steps/complete.html",
                    {"profile": profile},
                )
                response["HX-Push-Url"] = reverse("jobs:account")
                return response
            return redirect("jobs:account")

        # Update wizard progress
        profile.wizard_step = next_index
        profile.save()

        # Return next step
        next_step = WIZARD_STEPS[next_index]

        # For non-HTMX requests (like skills form), redirect to wizard
        if not is_htmx:
            return redirect("jobs:impact_wizard")

        context = self._get_step_context(profile, next_index, next_step)
        template = f"jobs/impact_wizard/steps/{next_step['slug'].replace('-', '_')}.html"

        response = TemplateResponse(request, template, context)

        # Update URL for browser history
        response["HX-Push-Url"] = reverse(
            "jobs:impact_wizard_step", kwargs={"step_slug": next_step["slug"]}
        )

        return response

    def _get_step_index(self, step_slug):
        """Get index of step by slug."""
        for i, step in enumerate(WIZARD_STEPS):
            if step["slug"] == step_slug:
                return i
        return None

    def _get_step_context(self, profile, step_index, current_step):
        """Build context for a step."""
        context = {
            "profile": profile,
            "current_step": current_step,
            "step_index": step_index,
            "steps": WIZARD_STEPS,
            "prev_step": WIZARD_STEPS[step_index - 1] if step_index > 0 else None,
            "next_step": (
                WIZARD_STEPS[step_index + 1]
                if step_index < len(WIZARD_STEPS) - 1
                else None
            ),
        }

        # Add step-specific context
        if current_step["slug"] == "impact-areas":
            context["categories"] = Category.objects.all()
            context["selected_areas"] = list(
                profile.impact_areas.values_list("id", flat=True)
            )

        elif current_step["slug"] == "work-style":
            context["work_styles"] = SeekerProfile.WorkStyle.choices

        elif current_step["slug"] == "experience":
            context["experience_levels"] = SeekerProfile.ExperienceLevel.choices

        elif current_step["slug"] == "skills":
            context["skill_categories"] = SKILL_CATEGORIES
            context["categorized_skills"] = get_categorized_skill_choices()
            context["selected_skills"] = profile.skills or []

        elif current_step["slug"] == "preferences":
            context["remote_choices"] = SeekerProfile.RemotePreference.choices
            context["job_types"] = [
                ("full-time", "Full-time"),
                ("part-time", "Part-time"),
                ("contract", "Contract"),
                ("internship", "Internship"),
            ]

        elif current_step["slug"] == "summary":
            # Prepare summary data
            context["selected_areas"] = profile.impact_areas.all()
            context["skill_labels"] = self._get_skill_labels(profile.skills or [])

        return context

    def _get_skill_labels(self, skill_slugs):
        """Convert skill slugs to labels."""
        from ..constants.skills import get_skill_label

        return [get_skill_label(slug) for slug in skill_slugs]

    def _process_step(self, request, profile, step_slug):
        """Process step submission. Returns (success, errors)."""
        data = request.POST

        if step_slug == "welcome":
            # No data to process, just move forward
            return True, None

        elif step_slug == "impact-areas":
            area_ids = data.getlist("impact_areas")
            if not area_ids:
                return False, {"impact_areas": "Please select at least one impact area."}
            if len(area_ids) > 5:
                return False, {"impact_areas": "Please select up to 5 impact areas."}

            profile.impact_areas.set(area_ids)
            profile.save()
            return True, None

        elif step_slug == "work-style":
            work_style = data.get("work_style")
            if not work_style:
                return False, {"work_style": "Please select your work style."}

            profile.work_style = work_style
            profile.save()
            return True, None

        elif step_slug == "experience":
            experience_level = data.get("experience_level")
            if not experience_level:
                return False, {"experience_level": "Please select your experience level."}

            profile.experience_level = experience_level
            profile.save()
            return True, None

        elif step_slug == "skills":
            skills = data.getlist("skills")
            if len(skills) < 3:
                return False, {"skills": "Please select at least 3 skills."}
            if len(skills) > 15:
                return False, {"skills": "Please select up to 15 skills."}

            profile.skills = skills
            profile.save()
            return True, None

        elif step_slug == "preferences":
            profile.remote_preference = data.get("remote_preference", "")
            profile.job_types = data.getlist("job_types")

            # Parse salary
            salary_min = data.get("salary_min")
            salary_max = data.get("salary_max")
            if salary_min:
                try:
                    profile.salary_min = int(salary_min.replace(",", "").replace("$", ""))
                except ValueError:
                    pass
            if salary_max:
                try:
                    profile.salary_max = int(salary_max.replace(",", "").replace("$", ""))
                except ValueError:
                    pass

            # Location preferences
            locations = data.getlist("locations")
            profile.location_preferences = locations

            profile.save()
            return True, None

        elif step_slug == "story":
            impact_statement = data.get("impact_statement", "").strip()
            if len(impact_statement) < 20:
                return False, {
                    "impact_statement": "Please write at least a few sentences about your motivation."
                }
            if len(impact_statement) > 500:
                return False, {
                    "impact_statement": "Please keep your story under 500 characters."
                }

            profile.impact_statement = impact_statement
            profile.save()
            return True, None

        elif step_slug == "assessment":
            # Optional step - store answers
            answers = {}
            for key in ["time_horizon", "org_size", "risk_tolerance", "theory_of_change"]:
                if data.get(key):
                    answers[key] = data.get(key)

            profile.assessment_answers = answers
            profile.save()
            return True, None

        elif step_slug == "summary":
            # Final confirmation - calculate completeness
            profile.profile_completeness = profile.calculate_completeness()
            profile.wizard_completed = True
            profile.save()
            return True, None

        return True, None


class ImpactWizardSkipStepView(LoginRequiredMixin, View):
    """Handle skipping optional steps."""

    def post(self, request, step_slug):
        profile = get_or_create_seeker_profile(request.user)

        # Find current step and move to next
        for i, step in enumerate(WIZARD_STEPS):
            if step["slug"] == step_slug:
                next_index = i + 1
                if next_index < len(WIZARD_STEPS):
                    profile.wizard_step = next_index
                    profile.save()

                    next_step = WIZARD_STEPS[next_index]
                    return HttpResponseRedirect(
                        reverse(
                            "jobs:impact_wizard_step",
                            kwargs={"step_slug": next_step["slug"]},
                        )
                    )
                break

        return redirect("jobs:impact_wizard")


class ImpactProfileView(LoginRequiredMixin, TemplateView):
    """View and edit the completed Impact Profile."""

    template_name = "jobs/impact_wizard/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = get_or_create_seeker_profile(self.request.user)

        from ..constants.skills import get_skill_label

        context["profile"] = profile
        context["skill_labels"] = [get_skill_label(s) for s in (profile.skills or [])]
        context["categories"] = Category.objects.all()
        context["work_styles"] = SeekerProfile.WorkStyle.choices
        context["experience_levels"] = SeekerProfile.ExperienceLevel.choices

        return context
