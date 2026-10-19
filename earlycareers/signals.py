from allauth.account.signals import user_signed_up
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from jobs.models import ConsumerFunnelEvent
from jobs.services.funnel import record_consumer_event
from .models import DirectoryEvent
from .services import finish_saves, recent_intent, record


@receiver(user_signed_up, dispatch_uid='early_careers_signup')
def directory_signup(sender, request, user, **kwargs):
    intent = recent_intent(request)
    if not intent:
        return
    record(request, DirectoryEvent.Kind.SIGNUP_COMPLETE, user=user, unique=True,
           metadata={'placement': intent.get('placement', 'directory')})
    event = record_consumer_event(ConsumerFunnelEvent.Event.ACCOUNT_CREATED, user=user,
                                  dedupe_key=f'account-created:{user.pk}')
    if event:
        event.metadata = {**(event.metadata or {}), 'entry_point': 'early_careers'}
        event.save(update_fields=['metadata'])


@receiver(user_logged_in, dispatch_uid='early_careers_login')
def directory_login(sender, request, user, **kwargs):
    finish_saves(request, user)
