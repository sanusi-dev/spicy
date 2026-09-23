from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.users.models import CustomUser

TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(STORAGES=TEST_STORAGES)
class TestViewBase(TestCase):
    pass


class TestLoginRequiredViewBase(TestViewBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.client = Client()
        cls.authenticated_client = Client()
        cls.user = CustomUser.objects.create_user(username="testing@example.com", password="12345")
        cls.user.groups.add(mgr)
        cls.authenticated_client.login(username="testing@example.com", password="12345")

    def _assert_logged_in_200(self, url):
        response = self.authenticated_client.get(url)
        self.assertEqual(response.status_code, 200)
