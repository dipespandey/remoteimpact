from .views.roundup_issue import roundup_issue
from .views.roundup import RoundupView, RoundupConfirmView, RoundupUnsubscribeView
from django.urls import path
from django.views.generic import RedirectView
from . import views
from .views.resources import home as resources_home
from .views import shortlists, tracking, employers, applications, developers
from .api import mcp as api_mcp, rest as api_rest
from .views.agent_report import AgentEvidenceReportView
from .views.hiring_evidence import HiringEvidenceView
from .views.nonprofit_hiring import NonprofitHiringLocationsView, nonprofit_hiring_locations_data
from .views.early_careers import early_careers, early_careers_join, early_careers_save, early_careers_outbound, early_careers_access_report, early_careers_embed
from earlycareers.feeds import EarlyCareersFeed
from .feeds import LatestJobsFeed, CategoryJobsFeed, IndeedXMLFeed, LinkedInXMLFeed

app_name = "jobs"

urlpatterns = [
    path("shortlist/", shortlists.shortlist, name="shortlist"),
    path("shortlist/share/", shortlists.share, name="share_shortlist"),
    path("collections/tech-for-impact/", shortlists.tech_collection, name="tech_collection"),
    path("shortlists/<uuid:token>/", shortlists.shared, name="shared_shortlist"),
    path("shortlists/<uuid:token>/revoke/", shortlists.revoke, name="revoke_shortlist"),
    path("jobs/<slug:slug>/interest/", shortlists.interest, name="job_interest"),
    path("employers/jobs/<slug:slug>/shortlist/", shortlists.employer_shortlist, name="employer_shortlist"),
    path("employers/jobs/<slug:slug>/shortlist/<int:pk>/stage/", shortlists.employer_stage, name="employer_interest_stage"),
    # Public
    path("early-careers/", early_careers, name="early_careers"),
    path("early-careers/access-report/", early_careers_access_report, name="early_careers_access_report"),
    path("early-careers/embed/", early_careers_embed, name="early_careers_embed"),
    path("early-careers/join/", early_careers_join, name="early_careers_join"),
    path("early-careers/<slug:slug>/save/", early_careers_save, name="early_careers_save"),
    path("early-careers/<slug:slug>/visit/", early_careers_outbound, name="early_careers_outbound"),
    path("insights/remote-impact-jobs/2026-10-17/", AgentEvidenceReportView.as_view(), name="agent_evidence_report"),
    path("", resources_home, name="home"),
    path("resources/", views.ResourcesView.as_view(), name="resources"),
    path("resources/remote-impact-jobs/", views.PartnerResourcesView.as_view(), name="partner_resources"),
    path("insights/remote-impact-jobs/", views.ImpactJobsSnapshotView.as_view(), name="impact_jobs_snapshot"),
    path("insights/nonprofit-hiring-locations/", NonprofitHiringLocationsView.as_view(), name="nonprofit_hiring_locations"),
    path("insights/nonprofit-hiring-locations/data.json", nonprofit_hiring_locations_data, name="nonprofit_hiring_locations_data"),
    path("insights/<slug:topic>/", HiringEvidenceView.as_view(), name="hiring_evidence"),
    path("how-we-check/", views.HowWeCheckView.as_view(), name="how_we_check"),
    path("domains/", views.AllDomainsView.as_view(), name="all_domains"),
    path("salaries/", views.SalaryBenchmarksView.as_view(), name="salary_benchmarks"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("privacy/", views.PrivacyPolicyView.as_view(), name="privacy"),
    path("terms/", views.TermsOfServiceView.as_view(), name="terms"),
    path("pricing/", views.PublicPricingView.as_view(), name="public_pricing"),
    # Jobs
    path("jobs/", views.JobListView.as_view(), name="job_list"),
    path("jobs/post/", views.PostJobView.as_view(), name="post_job"),
    path("employers/", employers.EmployerLandingView.as_view(), name="employers"),
    path("employers/pricing/", RedirectView.as_view(pattern_name="jobs:employers", permanent=True), name="employer_pricing"),
    path("employers/claim/<slug:slug>/", employers.ClaimOrganizationView.as_view(), name="claim_organization"),
    path("employers/claim/verify/<str:token>/", employers.ClaimVerifyView.as_view(), name="claim_verify"),
    path("employers/report/<str:token>/", employers.EmployerReportView.as_view(), name="employer_report"),
    path("jobs/<slug:slug>/feature/", employers.FeatureJobView.as_view(), name="feature_job"),
    path("jobs/<slug:slug>/feature/success/", employers.FeatureJobSuccessView.as_view(), name="feature_job_success"),
    path("jobs/category/<slug:slug>/", views.CategoryLandingView.as_view(), name="category_landing"),
    path("jobs/<slug:slug>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<slug:slug>/application-guide/", views.ApplicationGuideView.as_view(), name="application_guide"),
    path("jobs/<slug:slug>/v/", tracking.JobViewBeaconView.as_view(), name="job_view_beacon"),
    path("jobs/<slug:slug>/go/", tracking.JobApplyRedirectView.as_view(), name="job_apply_redirect"),
    path("t/rank/", tracking.ranking_click, name="ranking_click"),
    path("jobs/<slug:slug>/og-image/", views.JobOGImageView.as_view(), name="job_og_image"),
    path("jobs/<slug:slug>/save/", views.SaveJobView.as_view(), name="save_job"),
    path("jobs/<slug:slug>/checkout/", views.JobCheckoutView.as_view(), name="job_checkout"),
    path("jobs/<slug:slug>/edit/", views.JobEditView.as_view(), name="job_edit"),
    path("employers/plan/<slug:tier>/", views.OrgPlanCheckoutView.as_view(), name="org_plan_checkout"),
    path("account/plan/billing/", views.OrgBillingPortalView.as_view(), name="org_billing_portal"),
    path("applications/<int:pk>/job/", views.AppliedJobDetailView.as_view(), name="applied_job_detail"),
    # API
    path("api/organizations/search/", views.OrganizationSearchView.as_view(), name="organization_search"),
    # Account / Onboarding
    path("account/", views.DashboardView.as_view(), name="account"),
    path("account/delete/", views.AccountDeleteView.as_view(), name="account_delete"),
    path("account/cv/", views.UploadCVView.as_view(), name="upload_cv"),
    path("account/cv/remove/", views.RemoveCVView.as_view(), name="remove_cv"),
    path("account/cv/file/", applications.my_cv, name="my_cv"),
    path("applications/<int:pk>/cv/", applications.application_cv, name="application_cv"),
    path("account/company/edit/", views.EditCompanyProfileView.as_view(), name="edit_company_profile"),
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
    path("webhook/stripe/", views.StripeWebhookView.as_view(), name="stripe_webhook"),
    path("account/billing/", views.ProBillingPortalView.as_view(), name="pro_billing_portal"),
    # Tools
    path("tools/", views.ToolsIndexView.as_view(), name="tools_index"),
    path("tools/salary-to-hourly-calculator/", views.SalaryToHourlyView.as_view(), name="salary_to_hourly"),
    # Resources / AI (redirect to canonical URL)
    path(
        "resources/applicant-assistant/",
        RedirectView.as_view(url="/tools/cover-letter-generator/", permanent=True),
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
    # Content Hub — pillar & cluster guides
    path("resources/<slug:pillar_slug>/", views.PillarDetailView.as_view(), name="pillar_detail"),
    path("resources/<slug:pillar_slug>/<slug:cluster_slug>/", views.ClusterDetailView.as_view(), name="cluster_detail"),
    # Tools
    path(
        "tools/time-zone-overlap-finder/",
        views.TimezoneOverlapView.as_view(),
        name="timezone_overlap",
    ),
    # Tools
    path(
        "tools/cost-of-living-comparison/",
        views.CostOfLivingComparisonView.as_view(),
        name="cost_of_living_comparison",
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
    # Weekly roundup has separate explicit consent from legacy product updates.
    path("newsletter/issues/<slug:issue_slug>/", roundup_issue, name="roundup_issue"),
    path("newsletter/", RoundupView.as_view(), name="roundup"),
    path("newsletter/confirm/<str:token>/", RoundupConfirmView.as_view(), name="roundup_confirm"),
    path("newsletter/stop/<str:token>/", RoundupUnsubscribeView.as_view(), name="roundup_unsubscribe"),
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
    path("jobs/<slug:slug>/apply/", views.ApplicationCreateView.as_view(), name="apply_job"),
    path("jobs/<slug:slug>/applications/", views.EmployerJobApplicationsView.as_view(), name="employer_job_applications"),
    path("applications/", views.MyApplicationsView.as_view(), name="my_applications"),
    # Tools
    path("tools/resignation-letter-generator/", views.ResignationLetterView.as_view(), name="resignation_letter"),
    path("tools/resignation-letter-generator/generate/", views.ResignationLetterGenerateView.as_view(), name="resignation_letter_generate"),
    # RSS Feeds
    path("feed/jobs/", LatestJobsFeed(), name="jobs_feed"),
    path("feed/early-careers/", EarlyCareersFeed(), name="early_careers_feed"),
    path("feed/jobs.json", tracking.jobs_json_feed, name="jobs_json_feed"),
    # Jobs API and MCP server (see /developers/)
    path("developers/", developers.DevelopersView.as_view(), name="developers"),
    path("api/v1/jobs/", api_rest.jobs_list, name="api_jobs"),
    path("api/v1/jobs/<slug:slug>/", api_rest.job_detail, name="api_job"),
    path("api/v1/organizations/<slug:slug>/", api_rest.organization_detail, name="api_organization"),
    path("api/v1/areas/", api_rest.areas, name="api_areas"),
    path("api/v1/openapi.json", api_rest.openapi, name="api_openapi"),
    path("mcp/", api_mcp.endpoint, name="mcp"),
    path(".well-known/mcp/server-card.json", api_mcp.server_card, name="mcp_server_card"),
    path("feed/jobs/category/<slug:slug>/", CategoryJobsFeed(), name="category_feed"),
    # Job Aggregator Feeds (Indeed, LinkedIn, etc.)
    path("feed/indeed.xml", IndeedXMLFeed.as_view(), name="indeed_feed"),
    path("feed/linkedin.xml", LinkedInXMLFeed.as_view(), name="linkedin_feed"),
    # Referral System
    path("referral/", views.ReferralDashboardView.as_view(), name="referral_dashboard"),
    # Job Alerts
    path("alerts/preview/", views.JobScoutPreviewView.as_view(), name="job_scout_preview"),
    path("alerts/preview/status/<uuid:task_id>/", views.ScoutPreviewStatusView.as_view(), name="scout_preview_status"),
    path("alerts/contact/", views.ScoutContactCaptureView.as_view(), name="scout_contact_capture"),
    path("alerts/contact/verify/<str:token>/", views.ScoutContactVerifyView.as_view(), name="scout_contact_verify"),
    path("alerts/signup/", views.JobScoutSignupStartView.as_view(), name="job_scout_signup_start"),
    path("alerts/", views.JobAlertListView.as_view(), name="job_alerts"),
    path("alerts/create/", views.JobAlertCreateView.as_view(), name="job_alert_create"),
    path("alerts/<int:pk>/retry-email/", views.JobAlertRetryEmailView.as_view(), name="job_alert_retry_email"),
    path(
        "alerts/unsubscribe/<str:token>/",
        views.JobAlertUnsubscribeView.as_view(),
        name="job_alert_unsubscribe",
    ),
    path("alerts/<int:pk>/edit/", views.JobAlertUpdateView.as_view(), name="job_alert_edit"),
    path("alerts/<int:pk>/delete/", views.JobAlertDeleteView.as_view(), name="job_alert_delete"),
    path("alerts/<int:pk>/toggle/", views.JobAlertToggleView.as_view(), name="job_alert_toggle"),
    path("alerts/deliveries/<int:pk>/feedback/", views.JobAlertFeedbackView.as_view(), name="job_alert_feedback"),
    path("alerts/pro/subscribe/", views.JobScoutSubscribeView.as_view(), name="job_scout_subscribe"),
    path("alerts/pro/success/", views.JobScoutSubscribeSuccessView.as_view(), name="job_scout_subscribe_success"),
    # Talent Invitations
    path("invitations/", views.InvitationInboxView.as_view(), name="invitations"),
    path("invitations/<int:pk>/decline/", views.InvitationDeclineView.as_view(), name="invitation_decline"),
    # Visibility Settings
    path("profile/visibility/", views.VisibilitySettingsView.as_view(), name="visibility_settings"),
    # SEO alias for cover letter generator
    path(
        "tools/cover-letter-generator/null",
        RedirectView.as_view(url="/tools/cover-letter-generator/", permanent=True),
        name="cover_letter_generator_null_redirect",
    ),
    path("tools/cover-letter-generator/", views.ApplicantAssistantView.as_view(), name="cover_letter_generator"),
    # Job Description Generator
    path("tools/job-description-generator/", views.JobDescriptionGeneratorView.as_view(), name="jd_generator"),
    path("tools/job-description-generator/generate/", views.JobDescriptionGenerateView.as_view(), name="jd_generator_generate"),
    path("tools/word-counter/", views.WordCounterView.as_view(), name="word_counter"),
    path("tools/pomodoro-timer/", views.PomodoroTimerView.as_view(), name="pomodoro_timer"),
    path("tools/interview-prep/", views.InterviewPrepView.as_view(), name="interview_prep"),
    path(
        "tools/interview-prep/null",
        RedirectView.as_view(url="/tools/interview-prep/", permanent=True),
        name="interview_prep_null_redirect",
    ),
    path("tools/interview-prep/generate/", views.InterviewPrepGenerateView.as_view(), name="interview_prep_generate"),
    path("tools/freelance-rate-calculator/", views.FreelanceRateView.as_view(), name="freelance_rate"),
    path("tools/salary-negotiation-script/", views.SalaryNegotiationView.as_view(), name="salary_negotiation"),
    path("tools/salary-negotiation-script/generate/", views.SalaryNegotiationGenerateView.as_view(), name="salary_negotiation_generate"),
    path("tools/skills-gap-analyzer/", views.SkillsGapView.as_view(), name="skills_gap"),
    path("tools/skills-gap-analyzer/analyze/", views.SkillsGapAnalyzeView.as_view(), name="skills_gap_analyze"),
    path("tools/pto-calculator/", views.PTOCalculatorView.as_view(), name="pto_calculator"),
    path("tools/thank-you-email-generator/", views.ThankYouEmailView.as_view(), name="thank_you_email"),
    path("tools/thank-you-email-generator/generate/", views.ThankYouEmailGenerateView.as_view(), name="thank_you_email_generate"),
    path("tools/pay-raise-calculator/", views.PayRaiseCalculatorView.as_view(), name="pay_raise_calculator"),
    path("tools/remote-work-readiness-quiz/", views.RemoteReadinessQuizView.as_view(), name="remote_readiness_quiz"),
    # Organization pages
    path("organizations/", views.OrganizationDirectoryView.as_view(), name="organization_directory"),
    path("org/<slug:slug>/", views.OrganizationProfileView.as_view(), name="organization_profile"),
    path("org/<slug:slug>/logo.png", tracking.organization_logo, name="organization_logo"),
    # Location / Region pages
    path("remote-jobs/<slug:region_slug>/", views.RegionJobsView.as_view(), name="region_jobs"),
    # Programmatic SEO: Role-based landing pages
    path("careers/", views.AllRolePagesView.as_view(), name="all_role_pages"),
    # Programmatic SEO: Keyword-based landing pages
    path("browse/", views.AllKeywordPagesView.as_view(), name="all_keyword_pages"),
    path("impact/<slug:keyword_slug>/", views.KeywordJobsView.as_view(), name="keyword_jobs"),
    # Role pages (catch-all, must be last)
    path("<slug:role_slug>/", views.RoleJobsView.as_view(), name="role_jobs"),
]
