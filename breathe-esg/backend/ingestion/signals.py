from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile
from .tenancy import get_first_available_tenant


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if not created:
        return

    tenant = get_first_available_tenant()
    UserProfile.objects.get_or_create(
        user=instance,
        defaults={'tenant': tenant},
    )
