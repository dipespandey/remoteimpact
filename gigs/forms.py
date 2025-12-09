from django import forms

from .models import (
    Gig,
    GigApplication,
    GigInterest,
    PortfolioItem,
    Review,
    Trial,
)


class GigForm(forms.ModelForm):
    deliverables_text = forms.CharField(
        label="Deliverables",
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="One deliverable per line",
    )
    eligible_countries_text = forms.CharField(
        label="Eligible countries",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="Comma-separated ISO country codes. Required for country restricted gigs.",
    )
    rubric_text = forms.CharField(
        label="Rubric criteria",
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="One per line. Format: Label - Optional description",
    )

    class Meta:
        model = Gig
        fields = [
            "title",
            "category",
            "remote_policy",
            "timezone_overlap",
            "budget_fixed_cents",
            "currency",
            "trial_fee_cents",
            "trial_hours_cap",
            "trial_due_days",
            "definition_of_done",
            "brief_redacted",
            "brief_full",
            "nda_required",
            "requires_field_verification",
            "is_featured",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "category": forms.Select(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "remote_policy": forms.Select(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "timezone_overlap": forms.TextInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "budget_fixed_cents": forms.NumberInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "currency": forms.TextInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "trial_fee_cents": forms.NumberInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "trial_hours_cap": forms.NumberInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "trial_due_days": forms.NumberInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "definition_of_done": forms.Textarea(attrs={
                "rows": 3,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "brief_redacted": forms.Textarea(attrs={
                "rows": 3,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "brief_full": forms.Textarea(attrs={
                "rows": 3,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "nda_required": forms.CheckboxInput(attrs={
                "class": "w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-2 focus:ring-brand-300"
            }),
            "requires_field_verification": forms.CheckboxInput(attrs={
                "class": "w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-2 focus:ring-brand-300"
            }),
            "is_featured": forms.CheckboxInput(attrs={
                "class": "w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-2 focus:ring-brand-300"
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        if instance:
            self.fields["deliverables_text"].initial = "\n".join(
                instance.deliverables or []
            )
            self.fields["eligible_countries_text"].initial = ", ".join(
                instance.eligible_countries or []
            )
            if instance.rubric.exists():
                self.fields["rubric_text"].initial = "\n".join(
                    f"{c.label} - {c.description}" if c.description else c.label
                    for c in instance.rubric.all()
                )

    def clean(self):
        cleaned = super().clean()
        deliverables = [
            line.strip()
            for line in self.cleaned_data.get("deliverables_text", "").splitlines()
            if line.strip()
        ]
        if not deliverables:
            self.add_error("deliverables_text", "At least one deliverable is required.")

        remote_policy = cleaned.get("remote_policy")
        eligible_countries_text = cleaned.get("eligible_countries_text", "")
        eligible_countries = [
            c.strip().upper()
            for c in eligible_countries_text.split(",")
            if c.strip()
        ]
        if remote_policy == Gig.RemotePolicy.COUNTRY and not eligible_countries:
            self.add_error(
                "eligible_countries_text",
                "Country-restricted gigs must list at least one eligible country.",
            )
        cleaned["eligible_countries"] = eligible_countries
        cleaned["deliverables_list"] = deliverables

        rubric_lines = [
            line.strip()
            for line in self.cleaned_data.get("rubric_text", "").splitlines()
            if line.strip()
        ]
        if not rubric_lines:
            self.add_error("rubric_text", "Add at least one rubric criterion.")
        cleaned["rubric_lines"] = rubric_lines

        fee = cleaned.get("trial_fee_cents") or 0
        if fee < 5000:
            self.add_error("trial_fee_cents", "Trial fee must be at least $50.")

        hours_cap = cleaned.get("trial_hours_cap")
        if hours_cap is not None and hours_cap <= 0:
            self.add_error("trial_hours_cap", "Hours cap must be greater than zero.")

        return cleaned

    def save(self, commit=True):
        gig = super().save(commit=False)
        gig.deliverables = self.cleaned_data.get("deliverables_list", [])
        gig.eligible_countries = self.cleaned_data.get("eligible_countries", [])

        rubric_entries = []
        for line in self.cleaned_data.get("rubric_lines", []):
            if " - " in line:
                label, description = line.split(" - ", 1)
            else:
                label, description = line, ""
            rubric_entries.append({"label": label.strip(), "description": description})
        self.rubric_entries = rubric_entries

        if commit:
            gig.save()
        return gig


class PortfolioItemForm(forms.ModelForm):
    links_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="One link per line",
        label="Links",
    )
    tags_text = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="Comma-separated tags",
        label="Tags",
    )

    class Meta:
        model = PortfolioItem
        fields = ["title", "summary", "file", "visibility"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "summary": forms.Textarea(attrs={
                "rows": 3,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "file": forms.ClearableFileInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
            "visibility": forms.Select(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        if instance:
            self.fields["links_text"].initial = "\n".join(instance.links or [])
            self.fields["tags_text"].initial = ", ".join(instance.tags or [])

    def clean(self):
        cleaned = super().clean()
        cleaned["links_list"] = [
            line.strip()
            for line in self.cleaned_data.get("links_text", "").splitlines()
            if line.strip()
        ]
        cleaned["tags_list"] = [
            t.strip() for t in self.cleaned_data.get("tags_text", "").split(",") if t
        ]
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.links = self.cleaned_data.get("links_list", [])
        obj.tags = self.cleaned_data.get("tags_list", [])
        if commit:
            obj.save()
        return obj


class GigApplicationForm(forms.ModelForm):
    selected_portfolio_items = forms.ModelMultipleChoiceField(
        queryset=PortfolioItem.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        label="Attach portfolio",
    )

    class Meta:
        model = GigApplication
        fields = ["motivation", "selected_portfolio_items"]
        widgets = {
            "motivation": forms.Textarea(attrs={
                "rows": 4,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
        }

    def __init__(self, *args, **kwargs):
        seeker = kwargs.pop("seeker", None)
        super().__init__(*args, **kwargs)
        if seeker:
            self.fields["selected_portfolio_items"].queryset = PortfolioItem.objects.filter(
                seeker=seeker
            )


class TrialFundingForm(forms.ModelForm):
    class Meta:
        model = Trial
        fields = ["payment_reference"]
        widgets = {
            "payment_reference": forms.TextInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
            }),
        }

    def clean_payment_reference(self):
        ref = self.cleaned_data.get("payment_reference", "").strip()
        if not ref:
            raise forms.ValidationError("Payment reference is required.")
        return ref


class SubmissionForm(forms.Form):
    artifact_links_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
        help_text="One link per line",
        label="Artifact links",
    )
    artifact_files = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
    )
    geo_photos = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )
    receipts = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )
    call_logs = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )
    witness_contact = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )

    def clean_artifact_links_text(self):
        raw = self.cleaned_data.get("artifact_links_text", "")
        return [line.strip() for line in raw.splitlines() if line.strip()]


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=Review.Decision.choices,
        widget=forms.Select(attrs={
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        })
    )
    overall_comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
        }),
    )

    def __init__(self, *args, **kwargs):
        criteria = kwargs.pop("criteria", [])
        super().__init__(*args, **kwargs)
        self.criteria = criteria
        for criterion in criteria:
            self.fields[f"criterion_{criterion.id}"] = forms.IntegerField(
                min_value=0,
                max_value=criterion.max_score,
                initial=criterion.max_score,
                label=criterion.label,
                help_text=criterion.description,
                widget=forms.NumberInput(attrs={
                    "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition"
                }),
            )

    def cleaned_scores(self):
        return {
            str(criterion.id): self.cleaned_data.get(f"criterion_{criterion.id}")
            for criterion in self.criteria
        }


class GigInterestForm(forms.ModelForm):
    class Meta:
        model = GigInterest
        fields = ["email", "message"]
        widgets = {
            "email": forms.EmailInput(attrs={
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition",
                "placeholder": "your@email.com"
            }),
            "message": forms.Textarea(attrs={
                "rows": 4,
                "class": "w-full px-4 py-3 rounded-2xl bg-white border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-300 focus:outline-none transition",
                "placeholder": "Tell us what interests you about Impact Gigs (optional)..."
            }),
        }
