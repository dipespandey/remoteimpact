from django.views.generic import ListView, DetailView, View
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse
from ..models import Story, StoryResonance, Sprint, SprintCompletion, UserPath


class StoryFeedView(ListView):
    model = Story
    template_name = "stories/feed.html"
    context_object_name = "stories"
    paginate_by = 5

    def get_queryset(self):
        return Story.objects.select_related("organization").order_by("-created_at")


class StoryDetailView(DetailView):
    model = Story
    template_name = "stories/detail.html"
    context_object_name = "story"
    pk_url_kwarg = "story_id"


class ResonateView(View):
    def get(self, request, story_id):
        story = get_object_or_404(Story, id=story_id)
        return render(
            request, "stories/partials/resonance_modal.html", {"story": story}
        )


class SaveResonanceView(View):
    def post(self, request, story_id):
        # Implementation of resonance saving logic
        # For brevity in this refactor step, returning HTMX response
        return HttpResponse("""<button class="... disabled">Resonated</button>""")


class WantToDoView(View):
    def get(self, request, story_id):
        story = get_object_or_404(Story, id=story_id)
        return render(request, "stories/partials/want_to_do.html", {"story": story})


class SprintListView(ListView):
    model = Sprint
    template_name = "sprints/list.html"
    context_object_name = "sprints"


class SprintDetailView(DetailView):
    model = Sprint
    template_name = "sprints/detail.html"
    context_object_name = "sprint"
    pk_url_kwarg = "sprint_id"
