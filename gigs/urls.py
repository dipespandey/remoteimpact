from django.urls import path

from . import views

app_name = "gigs"

urlpatterns = [
    path("", views.GigListView.as_view(), name="gig_list"),
    path("<slug:slug>/apply/", views.GigApplyView.as_view(), name="gig_apply"),
    path("<slug:slug>/", views.GigDetailView.as_view(), name="gig_detail"),
    # Portfolio
    path("portfolio/", views.PortfolioListView.as_view(), name="portfolio_list"),
    path(
        "portfolio/new/", views.PortfolioCreateView.as_view(), name="portfolio_create"
    ),
    path(
        "portfolio/<int:pk>/edit/",
        views.PortfolioUpdateView.as_view(),
        name="portfolio_edit",
    ),
    path(
        "portfolio/<int:pk>/delete/",
        views.PortfolioDeleteView.as_view(),
        name="portfolio_delete",
    ),
    # Applications
    path(
        "applications/<int:pk>/",
        views.ApplicationDetailView.as_view(),
        name="application_detail",
    ),
    # Employer
    path(
        "employer/dashboard/",
        views.EmployerDashboardView.as_view(),
        name="employer_dashboard",
    ),
    path("employer/gigs/new/", views.GigCreateView.as_view(), name="gig_create"),
    path(
        "employer/gigs/<int:pk>/applications/",
        views.GigApplicationListView.as_view(),
        name="gig_applications",
    ),
    path(
        "employer/applications/<int:pk>/",
        views.EmployerApplicationReviewView.as_view(),
        name="employer_application",
    ),
    # Staff
    path("staff/queue/", views.StaffQueueView.as_view(), name="staff_queue"),
]
