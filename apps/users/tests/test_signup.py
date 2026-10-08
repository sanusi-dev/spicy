import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.users.adapter import SpicyAccountAdapter
from apps.users.models import CustomUser


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SignupClosedTest(TestCase):
    """Signup stays closed; backoffice staff_create is the only provisioning path."""

    def test_adapter_reports_signup_closed(self):
        self.assertFalse(SpicyAccountAdapter().is_open_for_signup(None))

    def test_signup_url_renders_closed_page(self):
        response = self.client.get(reverse("account_signup"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertNotContains(response, "<form")

    def test_posting_signup_data_creates_no_user(self):
        response = self.client.post(
            reverse("account_signup"),
            {"username": "selfsignup", "password1": "Sup3r-Secret-Pass!", "password2": "Sup3r-Secret-Pass!"},
        )
        self.assertFalse(CustomUser.objects.filter(username="selfsignup").exists())
        self.assertEqual(response.status_code, 200)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AvatarUploadContentTest(TestCase):
    """A script renamed to .jpg is rejected by Pillow, not by extension alone."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="av", password="testpass123")
        self.client.login(username="av", password="testpass123")

    def test_renamed_script_is_rejected(self):
        payload = SimpleUploadedFile("photo.jpg", b"<html><script>alert(1)</script></html>")
        response = self.client.post(reverse("users:upload_profile_image"), {"avatar": payload})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Upload a valid image", response.json()["errors"])

    def test_real_image_is_accepted(self):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
        payload = SimpleUploadedFile("photo.png", buffer.getvalue())
        response = self.client.post(reverse("users:upload_profile_image"), {"avatar": payload})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name)
