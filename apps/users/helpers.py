import os

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

MAX_PROFILE_PICTURE_BYTES = 5 * 1024 * 1024


def validate_profile_picture(value):
    valid_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
        ".bmp",
    }
    file_extension = os.path.splitext(value.name)[1].lower()
    if file_extension not in valid_extensions:
        raise ValidationError(
            _("Please upload a valid image file! Supported types are {types}").format(
                types=", ".join(sorted(valid_extensions)),
            )
        )
    if value.size > MAX_PROFILE_PICTURE_BYTES:
        size_in_mb = value.size // 1024**2
        raise ValidationError(
            _("Maximum file size allowed is {limit} MB. Provided file is {size} MB.").format(
                limit=MAX_PROFILE_PICTURE_BYTES // 1024**2,
                size=size_in_mb,
            )
        )
