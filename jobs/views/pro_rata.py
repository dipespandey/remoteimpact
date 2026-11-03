from django import forms
from django.shortcuts import render
from django.views import View
from jobs.services.pro_rata_pay import calculate_pro_rata


class ProRataForm(forms.Form):
    annual_fte = forms.DecimalField(label='Advertised annual full-time salary', min_value=0, max_value=1000000000, decimal_places=2)
    full_time_hours = forms.DecimalField(label='Full-time hours per week', min_value=1, max_value=168, initial=35, decimal_places=2)
    actual_hours = forms.DecimalField(label='Your paid hours per week', min_value=1, max_value=168, initial=21, decimal_places=2)
    months = forms.DecimalField(label='Paid contract length in months', min_value=0.01, max_value=120, initial=12, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'input', 'inputmode': 'decimal'})

    def clean(self):
        data = super().clean()
        if all(key in data for key in self.fields):
            try:
                self.result = calculate_pro_rata(**data)
            except ValueError as error:
                raise forms.ValidationError(str(error))
        return data


class ProRataPayView(View):
    def get(self, request):
        return render(request, 'jobs/tools/pro_rata.html', {'form': ProRataForm()})

    def post(self, request):
        form = ProRataForm(request.POST)
        result = form.result if form.is_valid() else None
        response = render(request, 'jobs/tools/pro_rata.html', {'form': form, 'result': result})
        response['Cache-Control'] = 'private, no-store'
        response['X-Robots-Tag'] = 'noindex, follow'
        return response
