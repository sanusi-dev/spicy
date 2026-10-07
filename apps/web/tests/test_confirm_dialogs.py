from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ConfirmDialogContractTests(SimpleTestCase):
    def test_confirm_dialog_uses_text_nodes(self):
        source = (Path(settings.BASE_DIR) / "assets/javascript/confirm.js").read_text()
        self.assertIn("titleText: confirmTitle(element)", source)
        self.assertIn("text: confirmMessage(element)", source)
        self.assertNotIn("html: confirmMessage", source)

    def test_toasts_use_text_titles(self):
        source = (Path(settings.BASE_DIR) / "assets/javascript/toast.js").read_text()
        self.assertIn("titleText: message", source)
        self.assertNotIn("title: message", source)
