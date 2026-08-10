import io
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.infrastructure.sqlite_agent_memory import SQLiteAgentMemoryRepository
from chronos.schedule.agent_memory import AgentMemoryService


class AgentMemoryImportTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.service = AgentMemoryService(
            SQLiteAgentMemoryRepository(root / "chronos.sqlite3"),
            root / "imports",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_chatgpt_export_creates_reviewable_deduplicated_candidates(self) -> None:
        archive = _archive(
            [
                {
                    "id": "conversation-1",
                    "title": "Working preferences",
                    "mapping": {
                        "node-1": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {
                                    "parts": ["我通常上午做深度工作。\n我不喜欢下午开长会。"]
                                },
                            }
                        }
                    },
                }
            ]
        )

        imported = self.service.import_archive("chatgpt", "export.zip", archive)
        candidates = self.service.list_candidates()

        self.assertEqual(imported["messages_scanned"], 1)
        self.assertEqual(imported["candidates_created"], 2)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(Path(str(imported["archive_path"])).is_file())

        duplicate = self.service.import_archive("chatgpt", "again.zip", archive)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.service.list_candidates()), 2)

    def test_accepted_candidate_becomes_agent_context(self) -> None:
        archive = _archive(
            [
                {
                    "uuid": "claude-1",
                    "name": "Preferences",
                    "chat_messages": [
                        {"sender": "human", "text": "I prefer meetings after 2pm."}
                    ],
                }
            ]
        )
        self.service.import_archive("claude", "claude.zip", archive)
        candidate = self.service.list_candidates()[0]

        reviewed = self.service.review(str(candidate["candidate_id"]), True)

        self.assertEqual(reviewed["status"], "accepted")
        self.assertEqual(len(self.service.list_context()), 1)
        self.assertIn("meetings after 2pm", self.service.accepted_context())

    def test_generated_markdown_creates_categorized_candidates(self) -> None:
        profile = """# Personal Profile
## 偏好与习惯
- 我偏好上午进行深度工作
- 回答尽量直接，不需要过多铺垫
## 日程与精力规律
1. 下午两点以后再安排会议
## 隐私边界
- 不要保存密码或 API Key
""".encode()

        imported = self.service.import_document("chatgpt", "my-profile.md", profile)
        candidates = self.service.list_candidates()

        self.assertEqual(imported["messages_scanned"], 4)
        self.assertEqual(imported["candidates_created"], 4)
        self.assertTrue(str(imported["archive_path"]).endswith(".md"))
        self.assertEqual(
            {item["category"] for item in candidates},
            {"preference", "scheduling", "boundary"},
        )
        self.assertTrue(all(item["confidence"] == 0.9 for item in candidates))

        duplicate = self.service.import_document("chatgpt", "copy.md", profile)
        self.assertTrue(duplicate["duplicate"])

    def test_chatgpt_markdown_fence_and_nested_lists_are_supported(self) -> None:
        profile = """```markdown
# Personal Profile
## 工作与项目
- Chronos 设计方向包括：
  - Monitor 模块，用于感知用户是否工作。
  - Forecast 模型，用于预测任务真实耗时。
## 学习与研究方向
- 用户关注 AI Agent 架构。
## 设备与开发环境
- 用户使用 Mac 进行开发。
## GPT 自行增加的分类
- 用户对少女乐队文化有较高兴趣。
```
""".encode()

        imported = self.service.import_document("chatgpt", "profile1.md", profile)
        candidates = self.service.list_candidates()

        self.assertEqual(imported["candidates_created"], 5)
        self.assertEqual(
            {item["category"] for item in candidates},
            {"work", "learning", "environment", "personal"},
        )
        contents = {str(item["content"]) for item in candidates}
        self.assertIn(
            "Chronos 设计方向包括： Monitor 模块，用于感知用户是否工作。",
            contents,
        )
        self.assertNotIn("Chronos 设计方向包括：", contents)

    def test_retrieval_selects_relevant_accepted_context(self) -> None:
        profile = """# Profile
## 日程与精力规律
- 用户偏好上午安排需要专注的任务
## 设备与开发环境
- 用户使用 Linux 服务器运行模型
""".encode()
        self.service.import_document("chatgpt", "profile.md", profile)
        for candidate in self.service.list_candidates():
            self.service.review(str(candidate["candidate_id"]), True)

        selected = self.service.retrieve_context("帮我安排每周的深度工作任务")

        self.assertEqual(len(selected), 1)
        self.assertIn("上午", selected[0]["content"])
        self.assertGreater(selected[0]["score"], 0)

    def test_accepted_context_can_be_edited_and_forgotten(self) -> None:
        self.service.import_document(
            "chatgpt", "profile.md", "## 偏好\n- 用户偏好上午工作\n".encode()
        )
        candidate = self.service.list_candidates()[0]
        self.service.review(str(candidate["candidate_id"]), True)
        context = self.service.list_context()[0]

        updated = self.service.update_context(
            str(context["context_id"]), content="用户偏好下午工作"
        )

        self.assertEqual(updated["content"], "用户偏好下午工作")
        self.assertEqual(updated["revision"], 2)
        self.assertTrue(self.service.delete_context(str(context["context_id"])))
        self.assertEqual(self.service.list_context(), [])

    def test_new_snapshot_marks_possible_conflict(self) -> None:
        self.service.import_document(
            "chatgpt", "morning.md", "## 日程\n- 用户偏好上午安排会议\n".encode()
        )
        first = self.service.list_candidates()[0]
        self.service.review(str(first["candidate_id"]), True)

        imported = self.service.import_document(
            "chatgpt", "afternoon.md", "## 日程\n- 用户偏好下午安排会议\n".encode()
        )
        candidate = self.service.list_candidates()[0]

        self.assertEqual(imported["change_counts"]["possible_conflict"], 1)
        self.assertEqual(candidate["change_type"], "possible_conflict")
        self.assertIn("上午", candidate["related_content"])


def _archive(conversations: list[dict[str, object]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "conversations.json", json.dumps(conversations, ensure_ascii=False)
        )
    return stream.getvalue()
