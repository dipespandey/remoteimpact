from django.views.generic import ListView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.utils import timezone
from django.http import HttpResponseForbidden
from ..models import TalentInvitation, SeekerProfile


class InvitationInboxView(LoginRequiredMixin, ListView):
    template_name = "account/invitations.html"
    context_object_name = "invitations"
    paginate_by = 20

    def get_queryset(self):
        try:
            seeker = self.request.user.seeker_profile
        except SeekerProfile.DoesNotExist:
            return TalentInvitation.objects.none()

        qs = TalentInvitation.objects.filter(seeker=seeker).select_related(
            "organization", "job"
        ).order_by("-sent_at")

        # Mark unseen as viewed
        qs.filter(status=TalentInvitation.Status.SENT).update(
            status=TalentInvitation.Status.VIEWED,
            viewed_at=timezone.now(),
        )
        return qs


class InvitationDeclineView(LoginRequiredMixin, View):
    def post(self, request, pk):
        invitation = get_object_or_404(TalentInvitation, pk=pk)
        try:
            seeker = request.user.seeker_profile
        except SeekerProfile.DoesNotExist:
            return HttpResponseForbidden()
        if invitation.seeker_id != seeker.id:
            return HttpResponseForbidden()
        invitation.status = TalentInvitation.Status.DECLINED
        invitation.responded_at = timezone.now()
        invitation.save(update_fields=["status", "responded_at"])
        return redirect("jobs:invitations")
