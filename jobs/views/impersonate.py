"""Let staff see the site as a given user, read-only.

Staff pick a user at /admin/impersonate/ and browse as them. While impersonating, every
non-read request is refused (see ImpersonationReadOnlyMiddleware), so nothing can be posted,
paid, saved or emailed on the user's behalf. Staff and superuser accounts can't be impersonated,
and the user's last-login time is left untouched.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

SESSION_KEY = "impersonator_id"
BACKEND = "django.contrib.auth.backends.ModelBackend"


def _login_quietly(request, user):
    """Log in without touching the account's last_login, so analytics stay true."""
    last_login = user.last_login
    login(request, user, backend=BACKEND)
    if user.last_login != last_login:
        type(user).objects.filter(pk=user.pk).update(last_login=last_login)


def impersonate_search(request):
    query = (request.GET.get("q") or "").strip()
    users = []
    if query:
        users = (get_user_model().objects.filter(is_staff=False, is_superuser=False, is_active=True)
                 .filter(Q(email__icontains=query) | Q(username__icontains=query)
                         | Q(first_name__icontains=query) | Q(last_name__icontains=query))
                 .select_related("profile").order_by("-last_login")[:30])
    return render(request, "admin/impersonate.html", {"query": query, "users": users})


@require_POST
def impersonate_start(request, user_id):
    if not request.user.is_staff or request.session.get(SESSION_KEY):
        return HttpResponseForbidden("Only staff can do this, and not while already impersonating.")
    target = get_object_or_404(get_user_model(), pk=user_id, is_active=True)
    if target.is_staff or target.is_superuser:
        return HttpResponseForbidden("Staff accounts can't be impersonated.")
    staff_id = request.user.pk
    _login_quietly(request, target)
    # login() starts a fresh session for a different user, so record who we really are afterwards.
    request.session[SESSION_KEY] = staff_id
    return redirect(request.POST.get("next") or "/")


@require_POST
def impersonate_stop(request):
    staff_id = request.session.get(SESSION_KEY)
    if not staff_id:
        return redirect("/")
    staff = get_object_or_404(get_user_model(), pk=staff_id, is_staff=True)
    viewed = request.user.pk
    _login_quietly(request, staff)
    messages.info(request, "You're back in your own account.")
    return redirect(f"/admin/impersonate/?q={request.POST.get('q', '')}" if viewed else "/admin/impersonate/")
