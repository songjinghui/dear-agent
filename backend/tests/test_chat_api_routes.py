from companion.main import app
from tests.support import ApiFeatureTestCase


class ChatApiRouteTests(ApiFeatureTestCase):
    def test_memory_preview_route_accepts_post(self) -> None:
        route = next(
            (item for item in app.routes if getattr(item, "path", None) == "/api/chat/memory-preview"),
            None,
        )

        self.assertIsNotNone(route, "the frontend's pre-send memory preview endpoint must be registered")
        self.assertIn("POST", route.methods)

