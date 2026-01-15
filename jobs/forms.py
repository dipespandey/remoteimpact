from django import forms

from django_countries.widgets import CountrySelectWidget

from .models import Category, Job, Organization, UserProfile


class OnboardingTypeForm(forms.Form):
    account_type = forms.ChoiceField(
        choices=UserProfile.AccountType.choices,
        widget=forms.RadioSelect,
        label="I am here to...",
    )


class EmployerOnboardingForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "website", "description"]
        labels = {
            "name": "Company Name",
            "description": "What do you do? (Short bio)",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_class = "w-full px-4 py-3 rounded-xl bg-gray-50 border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:bg-white focus:ring-1 focus:ring-brand-500 transition"
        for field in self.fields.values():
            field.widget.attrs.update({"class": base_class})


class OrgImpactProfileForm(forms.ModelForm):
    """Form for organization impact profile during onboarding."""

    class Meta:
        model = Organization
        fields = [
            "organization_type",
            "founded_year",
            "team_size",
            "remote_culture",
            "impact_statement",
            "impact_metric_name",
            "impact_metric_value",
            "has_public_impact_report",
            "impact_report_url",
            "has_public_financials",
            "financials_url",
        ]
        labels = {
            "organization_type": "Organization type",
            "founded_year": "Year founded",
            "team_size": "Team size",
            "remote_culture": "Work culture",
            "impact_statement": "Your impact statement",
            "impact_metric_name": "Key impact metric",
            "impact_metric_value": "Metric value",
            "has_public_impact_report": "We publish an impact report",
            "impact_report_url": "Impact report URL",
            "has_public_financials": "Our financials are public",
            "financials_url": "Financials URL (990, annual report, etc.)",
        }
        widgets = {
            "impact_statement": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "We help reduce carbon emissions by providing renewable energy solutions to underserved communities...",
                }
            ),
            "impact_metric_name": forms.TextInput(
                attrs={"placeholder": "e.g., Lives saved, Trees planted, Students taught"}
            ),
            "impact_metric_value": forms.TextInput(
                attrs={"placeholder": "e.g., 50,000+, 1.2M, 10,000/year"}
            ),
            "impact_report_url": forms.URLInput(
                attrs={"placeholder": "https://yourorg.org/impact-report"}
            ),
            "financials_url": forms.URLInput(
                attrs={"placeholder": "https://yourorg.org/financials"}
            ),
            "founded_year": forms.NumberInput(
                attrs={"placeholder": "2019", "min": 1800, "max": 2030}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_class = "w-full px-4 py-3 rounded-xl bg-gray-50 border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:bg-white focus:ring-1 focus:ring-brand-500 transition"
        checkbox_class = "h-5 w-5 text-brand-600 border-gray-300 rounded focus:ring-brand-500"

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": checkbox_class})
            else:
                field.widget.attrs.update({"class": base_class})


class SeekerOnboardingForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["headline", "bio", "linkedin_url", "years_experience", "country"]
        labels = {
            "headline": "Professional Headline",
            "bio": "About Me",
            "years_experience": "Years of Experience",
            "country": "Country (for eligibility)",
        }
        widgets = {
            "bio": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "I am a product manager passionate about...",
                }
            ),
            "country": CountrySelectWidget(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_class = "w-full px-4 py-3 rounded-xl bg-gray-50 border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:bg-white focus:ring-1 focus:ring-brand-500 transition"
        for field in self.fields.values():
            field.widget.attrs.update({"class": base_class})


class JobSubmissionForm(forms.Form):
    """Collect job + organization details for manual postings."""

    organization_name = forms.CharField(
        label="Organization name",
        max_length=200,
        help_text="Who is hiring for this role?",
    )
    organization_website = forms.URLField(
        label="Organization website",
        required=False,
    )
    organization_description = forms.CharField(
        label="What does your organization do?",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    title = forms.CharField(
        label="Role title",
        max_length=200,
    )
    category = forms.ModelChoiceField(
        label="Impact area",
        queryset=Category.objects.none(),
        empty_label="Select a category",
    )
    job_type = forms.ChoiceField(
        label="Engagement type",
        choices=Job.JOB_TYPE_CHOICES,
    )
    location = forms.CharField(
        label="Location / time zone notes",
        max_length=200,
        required=False,
        initial="Remote",
    )

    description = forms.CharField(
        label="Role overview",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    requirements = forms.CharField(
        label="Key responsibilities & requirements",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    impact = forms.CharField(
        label="Impact you'll drive",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    benefits = forms.CharField(
        label="Benefits & perks",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    salary_currency = forms.CharField(
        label="Currency",
        max_length=3,
        initial="USD",
    )
    salary_min = forms.DecimalField(
        label="Salary / rate (min)",
        required=False,
        max_digits=10,
        decimal_places=2,
    )
    salary_max = forms.DecimalField(
        label="Salary / rate (max)",
        required=False,
        max_digits=10,
        decimal_places=2,
    )

    application_url = forms.URLField(
        label="Application URL",
        help_text="Link to apply or to your ATS",
    )
    application_email = forms.EmailField(
        label="Application email",
        required=False,
        help_text="Optional: email for direct applicants",
    )
    how_to_apply = forms.CharField(
        label="Application instructions",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    contact_email = forms.EmailField(
        label="Internal contact for this listing",
        required=False,
        help_text="For coordination; not shown publicly",
    )
    start_timeline = forms.CharField(
        label="Ideal start date / timeline",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.all()
        base_input = {
            "class": "w-full px-4 py-3 rounded-xl bg-gray-50 border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:bg-white focus:ring-1 focus:ring-brand-500 transition"
        }
        base_textarea = {
            "class": "w-full px-4 py-3 rounded-xl bg-gray-50 border border-gray-200 text-gray-900 placeholder-gray-400 focus:border-brand-500 focus:bg-white focus:ring-1 focus:ring-brand-500 transition"
        }

        input_placeholders = {
            "organization_name": "Example: Climate Forward Labs",
            "organization_website": "https://",
            "title": "Senior Product Manager",
            "location": "Remote within GMT-4 to GMT+2",
            "salary_currency": "USD",
            "salary_min": "70000",
            "salary_max": "110000",
            "application_url": "https://jobs.yourorg.com/apply",
            "application_email": "talent@yourorg.org",
            "contact_email": "ops@yourorg.org",
        }
        textarea_placeholders = {
            "organization_description": "We build tools that help cities decarbonize their fleets...",
            "description": "What this team is shipping, the outcomes, and collaboration style...",
            "requirements": "Top responsibilities, must-have skills, tools, and experience level...",
            "impact": "How this role advances your mission",
            "benefits": "Salary bands, stipends, learning budget, etc.",
            "how_to_apply": "Links to portfolio, deadline, or short answers you need",
            "start_timeline": "Start in May, interviews rolling, offer by April 15",
        }

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update(base_textarea)
                placeholder = textarea_placeholders.get(name)
                if placeholder:
                    field.widget.attrs.setdefault("placeholder", placeholder)
            else:
                field.widget.attrs.update(base_input)
                placeholder = input_placeholders.get(name)
                if placeholder:
                    field.widget.attrs.setdefault("placeholder", placeholder)

    def clean(self):
        cleaned = super().clean()
        salary_min = cleaned.get("salary_min")
        salary_max = cleaned.get("salary_max")
        if salary_min and salary_max and salary_max < salary_min:
            self.add_error(
                "salary_max",
                "Max should be greater than or equal to the minimum salary.",
            )
        return cleaned
