from django.contrib import admin
from .models import BlogPost


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at", "author_name")
    list_filter = ("status",)
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-published_at",)
