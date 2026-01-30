from django.urls import path
from . import views
from .feeds import LatestJobsFeed, CategoryJobsFeed

app_name = "jobs"

urlpatterns = [
    # Public
    path("", views.HomeView.as_view(), name="home"),
    path("resources/", views.ResourcesView.as_view(), name="resources"),
    path("domains/", views.AllDomainsView.as_view(), name="all_domains"),
    # Jobs
    path("jobs/", views.JobListView.as_view(), name="job_list"),
    path("jobs/post/", views.PostJobView.as_view(), name="post_job"),
    path("jobs/category/<slug:slug>/", views.CategoryLandingView.as_view(), name="category_landing"),
    path("jobs/<slug:slug>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<slug:slug>/og-image/", views.JobOGImageView.as_view(), name="job_og_image"),
    path("jobs/<slug:slug>/save/", views.SaveJobView.as_view(), name="save_job"),
    path("applications/<int:pk>/job/", views.AppliedJobDetailView.as_view(), name="applied_job_detail"),
    # API
    path("api/organizations/search/", views.OrganizationSearchView.as_view(), name="organization_search"),
    # Account / Onboarding
    path("account/", views.DashboardView.as_view(), name="account"),
    path("onboarding/", views.StartOnboardingView.as_view(), name="start_onboarding"),
    path(
        "onboarding/select-type/",
        views.OnboardingSelectTypeView.as_view(),
        name="onboarding_select_type",
    ),
    path(
        "onboarding/employer/",
        views.OnboardingEmployerView.as_view(),
        name="onboarding_employer",
    ),
    path(
        "onboarding/impact-profile/",
        views.OnboardingImpactProfileView.as_view(),
        name="onboarding_impact_profile",
    ),
    path(
        "onboarding/seeker/",
        views.OnboardingSeekerView.as_view(),
        name="onboarding_seeker",
    ),
    # Impact Profile Wizard
    path(
        "impact-profile/wizard/",
        views.ImpactWizardView.as_view(),
        name="impact_wizard",
    ),
    path(
        "impact-profile/wizard/<slug:step_slug>/",
        views.ImpactWizardStepView.as_view(),
        name="impact_wizard_step",
    ),
    path(
        "impact-profile/wizard/<slug:step_slug>/skip/",
        views.ImpactWizardSkipStepView.as_view(),
        name="impact_wizard_skip",
    ),
    path(
        "impact-profile/",
        views.ImpactProfileView.as_view(),
        name="impact_profile",
    ),
    path(
        "my-matches/",
        views.MyMatchesView.as_view(),
        name="my_matches",
    ),
    # Payment
    path(
        "payment/success/", views.PaymentSuccessView.as_view(), name="payment_success"
    ),
    path("payment/cancel/", views.PaymentCancelView.as_view(), name="payment_cancel"),
    # Resources / AI
    path(
        "resources/applicant-assistant/",
        views.ApplicantAssistantView.as_view(),
        name="applicant_assistant",
    ),
    path(
        "resources/applicant-assistant/generate/",
        views.ApplicantAssistantGenerateView.as_view(),
        name="applicant_assistant_generate",
    ),
    path(
        "resources/applicant-assistant/subscribe/",
        views.AssistantSubscribeView.as_view(),
        name="assistant_subscribe",
    ),
    path(
        "resources/applicant-assistant/subscribe/success/",
        views.AssistantSubscribeSuccessView.as_view(),
        name="assistant_subscribe_success",
    ),
    # Stories & Sprints
    path("stories/", views.StoryFeedView.as_view(), name="stories_feed"),
    path(
        "stories/<uuid:story_id>/", views.StoryDetailView.as_view(), name="story_detail"
    ),
    path(
        "stories/<uuid:story_id>/resonate/",
        views.ResonateView.as_view(),
        name="resonate",
    ),
    path(
        "stories/<uuid:story_id>/save-resonance/",
        views.SaveResonanceView.as_view(),
        name="save_resonance",
    ),
    path(
        "stories/<uuid:story_id>/want-to-do/",
        views.WantToDoView.as_view(),
        name="want_to_do",
    ),
    path("contribute/", views.SprintListView.as_view(), name="sprints_list"),
    path(
        "contribute/<uuid:sprint_id>/",
        views.SprintDetailView.as_view(),
        name="sprint_detail",
    ),
    # Newsletter
    path(
        "newsletter/unsubscribe/<str:token>/",
        views.NewsletterUnsubscribeView.as_view(),
        name="newsletter_unsubscribe",
    ),
    path(
        "newsletter/preferences/",
        views.NewsletterPreferencesView.as_view(),
        name="newsletter_preferences",
    ),
    path(
        "newsletter/subscribe/",
        views.NewsletterSubscribeView.as_view(),
        name="newsletter_subscribe",
    ),
    # Talent Directory
    path("talent/", views.TalentDirectoryView.as_view(), name="talent_directory"),
    path("talent/<int:user_id>/", views.TalentProfileView.as_view(), name="talent_profile"),
    path("talent/<int:user_id>/invite/", views.TalentInviteView.as_view(), name="talent_invite"),
    path("jobs/<slug:slug>/apply/", views.ApplicationCreateView.as_view(), name="apply_job"),    path("applications/", views.MyApplicationsView.as_view(), name="my_applications"),
    # RSS Feeds
    path("feed/jobs/", LatestJobsFeed(), name="jobs_feed"),
    path("feed/jobs/category/<slug:slug>/", CategoryJobsFeed(), name="category_feed"),
    # Referral System
    path("referral/", views.ReferralDashboardView.as_view(), name="referral_dashboard"),
    # Job Alerts
    path("alerts/", views.JobAlertListView.as_view(), name="job_alerts"),
    path("alerts/create/", views.JobAlertCreateView.as_view(), name="job_alert_create"),
    path("alerts/<int:pk>/delete/", views.JobAlertDeleteView.as_view(), name="job_alert_delete"),
    path("alerts/<int:pk>/toggle/", views.JobAlertToggleView.as_view(), name="job_alert_toggle"),
    # Talent Invitations
    path("invitations/", views.InvitationInboxView.as_view(), name="invitations"),
    path("invitations/<int:pk>/decline/", views.InvitationDeclineView.as_view(), name="invitation_decline"),
    # Visibility Settings
    path("profile/visibility/", views.VisibilitySettingsView.as_view(), name="visibility_settings"),
]
