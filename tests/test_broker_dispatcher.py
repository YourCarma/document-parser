import unittest

from modules.broker.abc.abc import HandlerOutcome, TaskHandlerABC
from modules.broker.dispatcher import TaskDispatcher, build_default_dispatcher
from modules.broker.exceptions import UnknownTaskType
from modules.broker.handlers.translate import TranslateHandler
from modules.broker.schemas import TaskEnvelope, TranslatePayload
from modules.webhook_manager.schemas import TaskStatus


class DummyHandler(TaskHandlerABC):
    task_type = "document-parser.translate"
    payload_model = TranslatePayload

    async def handle(self, envelope: TaskEnvelope, task_key: str) -> HandlerOutcome:
        return HandlerOutcome(TaskStatus.READY)


class TaskDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.dispatcher = TaskDispatcher()
        self.dispatcher.register(DummyHandler)

    def test_resolve_exact_task_type(self):
        self.assertIs(
            self.dispatcher.resolve("document-parser.translate"), DummyHandler
        )

    def test_resolve_is_case_and_underscore_insensitive(self):
        self.assertIs(
            self.dispatcher.resolve("Document_Parser.Translate"), DummyHandler
        )

    def test_resolve_by_action_when_prefix_differs(self):
        self.assertIs(self.dispatcher.resolve("some-gateway.translate"), DummyHandler)

    def test_resolve_unknown_type_raises(self):
        with self.assertRaises(UnknownTaskType):
            self.dispatcher.resolve("document-parser.parse")

    def test_register_duplicate_raises(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register(DummyHandler)

    def test_default_dispatcher_contains_translate(self):
        dispatcher = build_default_dispatcher()

        self.assertEqual(dispatcher.task_types, ("document-parser.translate",))
        self.assertIs(
            dispatcher.resolve("document-parser.translate"), TranslateHandler
        )


if __name__ == "__main__":
    unittest.main()
