import os

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


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
                types=", ".join(valid_extensions),
            )
        )
    max_file_size = 5242880  # 5 MB limit
    if value.size > max_file_size:
        size_in_mb = value.size // 1024**2
        raise ValidationError(
            _("Maximum file size allowed is 5 MB. Provided file is {size} MB.").format(
                size=size_in_mb,
            )
        )
