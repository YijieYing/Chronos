from http import HTTPStatus
from unittest import TestCase

from chronos.api.schedule_server import SERVICE_CAPABILITIES, ScheduleRequestHandler


class ScheduleServerErrorTest(TestCase):
    def test_health_capabilities_include_reminder_api(self) -> None:
        self.assertIn("reminders-v1", SERVICE_CAPABILITIES)
        self.assertIn("chronos-log-v1", SERVICE_CAPABILITIES)
        self.assertIn("timeline-projections-v1", SERVICE_CAPABILITIES)
        self.assertIn("chronos-compiler-v1", SERVICE_CAPABILITIES)
        self.assertIn("adjustment-signals-v1", SERVICE_CAPABILITIES)

    def test_runtime_provider_error_becomes_structured_bad_gateway(self) -> None:
        handler = object.__new__(ScheduleRequestHandler)
        handler.path = "/api/v1/proposals"
        handler.v1_router = _FailingRouter()
        responses: list[tuple[dict[str, object], HTTPStatus]] = []
        handler._json = lambda payload, status=HTTPStatus.OK: responses.append(
            (payload, status)
        )

        handled = handler._dispatch_v1("POST", handler.path, {"text": "private"})

        self.assertTrue(handled)
        self.assertEqual(responses[0][1], HTTPStatus.BAD_GATEWAY)
        self.assertEqual(responses[0][0]["error"]["code"], "upstream_error")


class _FailingRouter:
    def dispatch(self, method, path, payload):
        raise RuntimeError("provider timeout")
