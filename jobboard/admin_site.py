"""A task-oriented admin shell; the full registry stays permission protected."""
from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _


class RemoteImpactAdminConfig(AdminConfig):
    default_site = "jobboard.admin_site.RemoteImpactAdminSite"


class RemoteImpactAdminSite(AdminSite):
    site_header = "Remote Impact"
    site_title = "Remote Impact admin"
    index_title = _("Workspace")

    # Model registration and permissions remain Django's responsibility. This is
    # a navigation shortlist, never an allowlist for access to records.
    sections = (
        (_("Hiring"), _("Manage listings, employers and applications."), (
            ("jobs.job", _("Jobs")), ("jobs.organization", _("Organizations")),
            ("jobs.application", _("Applications")),
            ("jobs.employerfunnelevent", _("Employer funnel events")),
        )),
        (_("People"), _("Find accounts and keep seeker profiles up to date."), (
            ("auth.user", _("Accounts")), ("jobs.userprofile", _("User profiles")),
            ("jobs.seekerprofile", _("Seeker profiles")),
            ("jobs.consumerfunnelevent", _("Customer funnel events")),
        )),
        (_("Scouts & subscriptions"), _("Review searches, contacts and paid access."), (
            ("jobs.jobalert", _("Job Scouts")), ("jobs.scoutcontact", _("Scout contacts")),
            ("jobs.assistantsubscription", _("Subscriptions")),
        )),
        (_("Publishing"), _("Manage articles, stories and the newsletter audience."), (
            ("blog.blogpost", _("Blog posts")), ("jobs.story", _("Stories")),
            ("jobs.roundupsubscriber", _("Newsletter audience")),
        )),
    )

    def get_urls(self):
        return [path("records/", self.admin_view(self.records), name="records")] + super().get_urls()

    def each_context(self, request):
        context = super().each_context(request)
        models = {
            f"{app['app_label']}.{model['object_name'].lower()}": model
            for app in context["available_apps"] for model in app["models"]
        }
        groups = []
        for title, description, entries in self.sections:
            items = []
            for key, label in entries:
                model = models.get(key)
                if model and (url := model.get("admin_url") or model.get("add_url")):
                    items.append({**model, "label": label, "url": url,
                                  "current": request.path.startswith(model.get("admin_url") or url)})
            if items:
                groups.append({"title": title, "description": description, "items": items})
        tools = [("admin_analytics", _("Analytics")), ("admin_outreach", _("Employer outreach"))]
        if "jobs.linkprospect" in models:
            tools.append(("admin_backlinks", _("Backlink outreach")))
        if request.user.has_perms(("jobs.view_consumerfunnelevent", "jobs.view_employerfunnelevent")):
            tools.append(("admin_diagnostics", _("Product analytics")))
        tools.extend((("admin_api_usage", _("API usage")), ("admin_impersonate", _("View as user"))))
        context["workspace_sections"] = groups
        context["workspace_tools"] = [
            {"label": label, "url": reverse(name),
             "current": request.resolver_match is not None and request.resolver_match.url_name == name}
            for name, label in tools
        ] if self.has_permission(request) else []
        context["workspace_home"] = request.path == reverse("admin:index")
        context["workspace_records"] = request.path == reverse("admin:records")
        return context

    def records(self, request):
        context = {**self.each_context(request), "title": _("Advanced records"),
                   "app_list": self.get_app_list(request)}
        request.current_app = self.name
        return TemplateResponse(request, "admin/records.html", context)
