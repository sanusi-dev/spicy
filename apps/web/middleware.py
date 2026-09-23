import json

# A followed 3xx drops response headers, so an HX-Trigger toast would be lost.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class MessagesMiddleware:
    def __call__(self, request):
        response = self.get_response(request)
        self._inject_messages(request, response)
        return response

    def __init__(self, get_response):
        self.get_response = get_response

    def _inject_messages(self, request, response):
        if not request.htmx:
            return

        if not hasattr(request, "_messages"):
            return

        storage = request._messages

        # Rewritten to 200 + HX-Redirect so HTMX navigates fully and queued messages survive.
        if response.status_code in _REDIRECT_STATUSES and storage:
            location = response.get("Location")
            if location:
                response.status_code = 200
                response["HX-Redirect"] = location
            return

        message_list = [{"message": m.message, "level": m.level_tag} for m in storage]
        if not message_list:
            return

        trigger_data = json.loads(response.get("HX-Trigger", "{}"))
        trigger_data["showMessages"] = message_list
        response["HX-Trigger"] = json.dumps(trigger_data)
