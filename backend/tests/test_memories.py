from companion.memory_routes import MemoryInput, create_memory, delete_memory, edit_memory, list_memories
from tests.support import ApiFeatureTestCase


class MemoryFeatureTests(ApiFeatureTestCase):
    def test_create_edit_list_and_delete_memory(self) -> None:
        created = create_memory(MemoryInput(
            content="喜欢先听完再讨论建议", memory_type="semantic", importance=4,
        ))
        edited = edit_memory(created["id"], MemoryInput(
            content="喜欢先听完再给建议", memory_type="semantic", importance=5,
        ))
        self.assertEqual(edited["content"], "喜欢先听完再给建议")
        self.assertEqual(len(list_memories()), 1)

        self.assertEqual(delete_memory(created["id"]), {"deleted": True})
        self.assertEqual(list_memories(), [])

    def test_private_memory_level_is_persisted(self) -> None:
        created = create_memory(MemoryInput(
            content="严格私密的记忆", memory_type="episodic", sensitivity="private",
        ))

        self.assertEqual(created["sensitivity"], "private")
        self.assertEqual(list_memories()[0]["sensitivity"], "private")
