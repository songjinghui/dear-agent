from companion.context_store import get_session_summary, save_session_summary
from companion.session_routes import SessionInput, create_session
from tests.support import ApiFeatureTestCase


class ContextStoreTests(ApiFeatureTestCase):
    def test_summary_is_persisted_with_coverage_sequence(self) -> None:
        session = create_session(SessionInput(title="摘要存储"))

        save_session_summary(session["id"], "用户在比较两个搬家方案。", through_sequence=12)

        summary = get_session_summary(session["id"])
        self.assertEqual(summary["summary"], "用户在比较两个搬家方案。")
        self.assertEqual(summary["through_sequence"], 12)

    def test_summary_update_replaces_old_summary_and_coverage(self) -> None:
        session = create_session(SessionInput(title="摘要更新"))
        save_session_summary(session["id"], "旧摘要", through_sequence=4)
        save_session_summary(session["id"], "更新后的摘要", through_sequence=9)

        summary = get_session_summary(session["id"])
        self.assertEqual(summary["summary"], "更新后的摘要")
        self.assertEqual(summary["through_sequence"], 9)
