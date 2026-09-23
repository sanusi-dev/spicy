import hashlib
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.users.helpers import validate_profile_picture


def _get_avatar_filename(instance, filename):
    """Generate a random profile-picture filename."""
    return f"profile-pictures/{uuid.uuid4()}.{filename.split('.')[-1]}"


class CustomUser(AbstractUser):
    avatar = models.FileField(upload_to=_get_avatar_filename, blank=True, validators=[validate_profile_picture])

    def __str__(self):
        return f"{self.get_full_name()} <{self.email or self.username}>"

    def get_display_name(self) -> str:
        if self.get_full_name().strip():
            return self.get_full_name()
        return self.email or self.username

    @property
    def avatar_url(self) -> str:
        if self.avatar:
            return self.avatar.url
        else:
            return f"https://www.gravatar.com/avatar/{self.gravatar_id}?s=128&d=identicon"

    @property
    def gravatar_id(self) -> str:
        # https://en.gravatar.com/site/implement/hash/
        return hashlib.md5(self.email.lower().strip().encode("utf-8")).hexdigest()

    @property
    def is_admin(self):
        return self.is_superuser or self.groups.filter(name="Spicy Admin").exists()

    @property
    def is_manager(self):
        return self.groups.filter(name="Spicy Manager").exists()

    @property
    def is_cashier(self):
        return self.groups.filter(name="Spicy Cashier").exists()

    @property
    def has_backoffice_access(self):
        return self.is_superuser or self.is_admin or self.is_manager

    @property
    def has_staff_role(self):
        return self.is_superuser or self.is_admin or self.is_manager or self.is_cashier
