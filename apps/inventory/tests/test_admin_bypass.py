from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase, override_settings

from apps.inventory.admin import StockEntryAdmin
from apps.inventory.models import StockEntry
from apps.users.models import CustomUser
from apps.utils.admin import dev_admin_bypass


class DevAdminBypassTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = CustomUser.objects.create_user(
            username="boss", password="testpass123", is_staff=True, is_superuser=True
        )
        self.staff = CustomUser.objects.create_user(username="clerk", password="testpass123", is_staff=True)
        self.entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT")
        self.entry.status = "SUBMITTED"
        self.entry.save(update_fields=["status", "updated_at"])
        self.admin = StockEntryAdmin(StockEntry, AdminSite())

    def _request(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        return request

    @override_settings(SPICY_DEV_ADMIN_BYPASS=True)
    def test_superuser_skips_lock_when_bypass_on(self):
        request = self._request(self.superuser)
        self.assertTrue(dev_admin_bypass(request))
        self.assertTrue(self.admin.has_change_permission(request, self.entry))
        self.assertTrue(self.admin.has_delete_permission(request, self.entry))
        self.assertEqual(list(self.admin.get_readonly_fields(request, self.entry)), [])

    @override_settings(SPICY_DEV_ADMIN_BYPASS=True)
    def test_non_superuser_stays_locked_when_bypass_on(self):
        request = self._request(self.staff)
        self.assertFalse(dev_admin_bypass(request))
        self.assertFalse(self.admin.has_change_permission(request, self.entry))
        self.assertFalse(self.admin.has_delete_permission(request, self.entry))

    @override_settings(SPICY_DEV_ADMIN_BYPASS=False)
    def test_superuser_stays_locked_when_bypass_off(self):
        request = self._request(self.superuser)
        self.assertFalse(dev_admin_bypass(request))
        self.assertFalse(self.admin.has_change_permission(request, self.entry))
        self.assertFalse(self.admin.has_delete_permission(request, self.entry))
        self.assertTrue(self.admin.get_readonly_fields(request, self.entry))
