from django.urls import path
from . import views

app_name = "jobs"

urlpatterns = [
    # Public
    path("", views.HomeView.as_view(), name="home"),
    path("resources/", views.ResourcesView.as_view(), name="resources"),
    path("domains/", views.AllDomainsView.as_view(), name="all_domains"),
    # Jobs
    path("jobs/", views.JobListView.as_view(), name="job_list"),
    path("jobs/post/", views.PostJobView.as_view(), name="post_job"),
    path("jobs/<slug:slug>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<slug:slug>/save/", views.SaveJobView.as_view(), name="save_job"),
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
        "onboarding/seeker/",
        views.OnboardingSeekerView.as_view(),
        name="onboarding_seeker",
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
]
