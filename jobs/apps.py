import os
import sys
import threading

from django.apps import AppConfig


def _warm_embedding_model():
    """Load the Scout query-embedding model off the request path.

    Semantic retrieval is skipped while the model is cold, so without this
    every web worker silently ran lexical-only matching.
    """
    import random
    import time

    # Stagger workers after a reload so model loading never starves live requests.
    time.sleep(random.uniform(10, 60))
    try:
        import torch

        torch.set_num_threads(1)  # four workers share four cores
        from jobs.services import embedding_service

        embedding_service.get_model()
    except Exception:  # noqa: BLE001 - matching falls back to lexical retrieval
        pass


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobs"
    verbose_name = "Jobs"

    def ready(self):
        # Ensure custom template tags are discoverable
        import jobs.templatetags  # noqa: F401
        import jobs.signals  # noqa: F401

        running_web = "gunicorn" in os.path.basename(sys.argv[0] if sys.argv else "")
        if running_web and os.getenv("SCOUT_WARM_EMBEDDINGS", "1") == "1":
            threading.Thread(target=_warm_embedding_model, name="warm-embeddings", daemon=True).start()
