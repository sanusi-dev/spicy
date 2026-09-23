from allauth.account.signals import email_confirmed, user_signed_up
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.mail import mail_admins
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from apps.users.models import CustomUser


@receiver(user_signed_up)
def handle_sign_up(request, user, **kwargs):
    _notify_admins_of_signup(user)


@receiver(email_confirmed)
def update_user_email(sender, request, email_address, **kwargs):
    """Make a confirmed email address the primary email."""
    email_address.set_as_primary()


def _notify_admins_of_signup(user):
    mail_admins(
        f"New signup: {settings.PROJECT_METADATA['NAME']}",
        f"Email: {user.email}",
        fail_silently=True,
    )


@receiver(pre_save, sender=CustomUser)
def remove_old_profile_picture_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    old_avatar = sender.objects.filter(pk=instance.pk).values_list("avatar", flat=True).first()
    if not old_avatar:
        return

    if old_avatar != instance.avatar.name and default_storage.exists(old_avatar):
        default_storage.delete(old_avatar)


@receiver(post_delete, sender=CustomUser)
def remove_profile_picture_on_delete(sender, instance, **kwargs):
    if instance.avatar and default_storage.exists(instance.avatar.name):
        default_storage.delete(instance.avatar.name)
