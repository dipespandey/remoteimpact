class ReferralMiddleware:
    """Capture ?ref=CODE from any URL and store in session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ref_code = request.GET.get("ref")
        if ref_code and not request.user.is_authenticated:
            request.session["referral_code"] = ref_code
        return self.get_response(request)
