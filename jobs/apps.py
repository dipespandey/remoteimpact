from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobs"
    verbose_name = "Jobs"

    def ready(self):
        # Ensure custom template tags are discoverable
        import jobs.templatetags  # noqa: F401
        import jobs.signals  # noqa: F401
