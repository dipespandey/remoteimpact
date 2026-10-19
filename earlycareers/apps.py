from django.apps import AppConfig


class EarlyCareersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'earlycareers'

    def ready(self):
        from . import signals  # noqa: F401
