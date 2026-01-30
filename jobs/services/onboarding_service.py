from django.shortcuts import redirect
from ..models import UserProfile, Organization, SeekerProfile
from ..utils import unique_slug


class OnboardingService:
    @staticmethod
    def get_user_profile(user):
        try:
            return user.profile
        except UserProfile.DoesNotExist:
            return None

    @staticmethod
    def get_redirect_for_state(user):
        """
        Determines where the user should be redirected based on their profile state.
        Returns URL name or None if onboarding is complete.
        """
        profile = OnboardingService.get_user_profile(user)
        if not profile:
            return "jobs:onboarding_select_type"

        if profile.account_type == UserProfile.AccountType.EMPLOYER:
            if not user.organizations.exists():
                return "jobs:onboarding_employer"

        if profile.account_type == UserProfile.AccountType.SEEKER:
            # Step 1: Basic seeker form (headline, bio, etc.)
            if not profile.headline:
                return "jobs:onboarding_seeker"
            # Step 2: Impact Profile wizard
            try:
                seeker_profile = user.seeker_profile
                if not seeker_profile.wizard_completed:
                    return "jobs:impact_wizard"
            except SeekerProfile.DoesNotExist:
                return "jobs:impact_wizard"

        # Onboarding complete
        return None

    @staticmethod
    def set_account_type(user, account_type):
        UserProfile.objects.update_or_create(
            user=user, defaults={"account_type": account_type}
        )

    @staticmethod
    def create_organization(user, form):
        org = form.save(commit=False)
        org.slug = unique_slug(Organization, org.name)
        org.save()
        org.members.add(user)
        return org
