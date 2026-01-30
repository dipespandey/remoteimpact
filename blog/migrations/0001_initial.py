import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BlogPost",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=300)),
                ("slug", models.SlugField(max_length=300, unique=True)),
                ("excerpt", models.TextField(help_text="Short summary for cards and meta description", max_length=500)),
                ("body", models.TextField(help_text="Full article content (HTML allowed)")),
                ("featured_image", models.ImageField(blank=True, null=True, upload_to="blog/")),
                ("featured_image_alt", models.CharField(blank=True, max_length=300)),
                ("author_name", models.CharField(default="Remote Impact Team", max_length=200)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published")], default="draft", max_length=20)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-published_at"],
                "verbose_name_plural": "Blog posts",
            },
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=models.Index(fields=["status", "-published_at"], name="blog_blogpo_status_idx"),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=models.Index(fields=["slug"], name="blog_blogpo_slug_idx"),
        ),
    ]
