from django.views.generic import ListView, DetailView
from .models import BlogPost


class BlogListView(ListView):
    model = BlogPost
    template_name = "blog/list.html"
    context_object_name = "posts"
    paginate_by = 12

    def get_queryset(self):
        return BlogPost.objects.filter(status=BlogPost.Status.PUBLISHED)


class BlogDetailView(DetailView):
    model = BlogPost
    template_name = "blog/detail.html"
    context_object_name = "post"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return BlogPost.objects.filter(status=BlogPost.Status.PUBLISHED)
