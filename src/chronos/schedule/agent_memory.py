"""Import account exports and turn personal statements into reviewable memory candidates."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from chronos.infrastructure.sqlite_agent_memory import SQLiteAgentMemoryRepository


SOURCES = {"chatgpt", "claude"}
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class AgentMemoryService:
    def __init__(
        self,
        repository: SQLiteAgentMemoryRepository,
        archive_root: str | Path = "data/agent-imports",
    ) -> None:
        self._repository = repository
        self._archive_root = Path(archive_root)
        self._context_loaded = False
        self._context_cache = ""

    def import_archive(self, source: str, filename: str, data: bytes) -> dict[str, object]:
        return self.import_document(source, filename, data)

    def import_document(self, source: str, filename: str, data: bytes) -> dict[str, object]:
        if source not in SOURCES:
            raise ValueError("source must be chatgpt or claude")
        if not data or len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError("import file must be between 1 byte and 50 MB")
        suffix = Path(filename).suffix.casefold()
        if suffix not in {".zip", ".md", ".markdown", ".txt"}:
            raise ValueError("import file must be ZIP, Markdown, or plain text")
        digest = sha256(data).hexdigest()
        existing = self._repository.get_import_by_hash(source, digest)
        if existing:
            return {**existing, "duplicate": True}
        import_id = str(uuid4())
        now = datetime.now(UTC)
        if suffix == ".zip":
            messages = _read_messages(source, data)
            candidates = _candidate_rows(source, import_id, messages, now)
            messages_scanned = len(messages)
        else:
            text = _decode_text(data)
            candidates = _markdown_candidate_rows(source, import_id, text, filename, now)
            messages_scanned = len(candidates)
        archive_path = self._store_document(source, filename, digest, data, now)
        created = self._repository.add_candidates(candidates)
        imported = self._repository.save_import(
            {
                "import_id": import_id,
                "source": source,
                "archive_name": Path(filename).name or f"{source}-export.zip",
                "archive_path": str(archive_path),
                "archive_hash": digest,
                "status": "processed",
                "messages_scanned": messages_scanned,
                "candidates_created": created,
                "created_at": now.isoformat(),
            }
        )
        return {**imported, "duplicate": False}

    def list_imports(self) -> list[dict[str, object]]:
        return self._repository.list_imports()

    def list_candidates(self, status: str = "pending") -> list[dict[str, object]]:
        return self._repository.list_candidates(status)

    def review(self, candidate_id: str, accepted: bool) -> dict[str, object]:
        item = self._repository.review(candidate_id, accepted)
        self._context_loaded = False
        return item

    def list_context(self) -> list[dict[str, object]]:
        return self._repository.list_context()

    def accepted_context(self) -> str:
        if self._context_loaded:
            return self._context_cache
        items = self._repository.list_context()
        lines = [f"- [{item['category']}] {item['content']}" for item in reversed(items)]
        self._context_cache = "\n".join(lines)[-12_000:]
        self._context_loaded = True
        return self._context_cache

    def _store_document(
        self, source: str, filename: str, digest: str, data: bytes, now: datetime
    ) -> Path:
        directory = self._archive_root / source
        directory.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(filename).stem)[:80] or source
        suffix = Path(filename).suffix.casefold()
        if suffix not in {".zip", ".md", ".markdown", ".txt"}:
            suffix = ".bin"
        target = directory / f"{now:%Y%m%dT%H%M%SZ}-{safe_stem}-{digest[:12]}{suffix}"
        target.write_bytes(data)
        return target.resolve()


def _read_messages(source: str, data: bytes) -> list[dict[str, object]]:
    stream = io.BytesIO(data)
    if not zipfile.is_zipfile(stream):
        raise ValueError("uploaded file is not a valid ZIP archive")
    messages: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(stream) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("uncompressed archive exceeds 200 MB")
            json_files = [
                item for item in archive.infolist()
                if not item.is_dir() and Path(item.filename).name.lower() == "conversations.json"
            ]
            if not json_files:
                raise ValueError("ZIP does not contain conversations.json")
            for item in json_files:
                payload = json.loads(archive.read(item))
                if source == "chatgpt":
                    messages.extend(_chatgpt_messages(payload))
                else:
                    messages.extend(_claude_messages(payload))
    except zipfile.BadZipFile as error:
        raise ValueError("uploaded file is not a readable ZIP archive") from error
    return messages


def _chatgpt_messages(payload: object) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for conversation in payload if isinstance(payload, list) else []:
        if not isinstance(conversation, dict):
            continue
        title = str(conversation.get("title") or "Untitled")
        conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or title)
        mapping = conversation.get("mapping", {})
        for node in mapping.values() if isinstance(mapping, dict) else []:
            if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                continue
            message = node["message"]
            author = message.get("author", {})
            if not isinstance(author, dict) or author.get("role") != "user":
                continue
            content = message.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text = "\n".join(part for part in parts if isinstance(part, str)).strip()
            if text:
                results.append({"text": text, "title": title, "ref": conversation_id})
    return results


def _claude_messages(payload: object) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for conversation in payload if isinstance(payload, list) else []:
        if not isinstance(conversation, dict):
            continue
        title = str(conversation.get("name") or conversation.get("title") or "Untitled")
        reference = str(conversation.get("uuid") or conversation.get("id") or title)
        items = conversation.get("chat_messages") or conversation.get("messages") or []
        for message in items if isinstance(items, list) else []:
            if not isinstance(message, dict):
                continue
            role = message.get("sender") or message.get("role")
            if role not in {"human", "user"}:
                continue
            text = str(message.get("text") or message.get("content") or "").strip()
            if text:
                results.append({"text": text, "title": title, "ref": reference})
    return results


def _candidate_rows(
    source: str,
    import_id: str,
    messages: list[dict[str, object]],
    now: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for message in messages:
        for statement in _personal_statements(str(message["text"])):
            normalized = re.sub(r"\s+", "", statement.casefold())
            fingerprint = sha256(normalized.encode("utf-8")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            rows.append(
                {
                    "candidate_id": str(uuid4()),
                    "source": source,
                    "fingerprint": fingerprint,
                    "category": _category(statement),
                    "content": statement,
                    "evidence": statement,
                    "source_ref": f"{message['title']} · {message['ref']}",
                    "confidence": 0.68,
                    "import_id": import_id,
                    "created_at": now.isoformat(),
                }
            )
            if len(rows) >= 300:
                return rows
    return rows


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Markdown import must use UTF-8 encoding") from error


def _markdown_candidate_rows(
    source: str,
    import_id: str,
    text: str,
    filename: str,
    now: datetime,
) -> list[dict[str, object]]:
    if len(text) > 1_000_000:
        raise ValueError("Markdown import exceeds 1,000,000 characters")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    heading = "personal"
    in_fence = False
    lines = text.splitlines()
    content_indexes = [index for index, value in enumerate(lines) if value.strip()]
    outer_fence_indexes: set[int] = set()
    if (
        len(content_indexes) >= 2
        and re.match(r"^```(?:markdown|md)\s*$", lines[content_indexes[0]].strip(), re.I)
        and lines[content_indexes[-1]].strip() == "```"
    ):
        # ChatGPT commonly wraps an otherwise valid Markdown document in a Markdown code fence.
        outer_fence_indexes = {content_indexes[0], content_indexes[-1]}
    parent_items: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        if index in outer_fence_indexes:
            continue
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line == "---":
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading_match:
            heading = _heading_category(heading_match.group(1))
            parent_items.clear()
            continue
        item_match = re.match(
            r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+)$",
            raw_line.expandtabs(4),
        )
        if not item_match:
            continue
        indentation = len(item_match.group(1))
        content = item_match.group(2).strip()
        while parent_items and parent_items[-1][0] >= indentation:
            parent_items.pop()
        if content.endswith(("：", ":")):
            parent_items.append((indentation, content))
            continue
        if parent_items:
            content = " ".join(parent for _, parent in parent_items) + " " + content
        if not _usable_markdown_item(content):
            continue
        normalized = re.sub(r"\s+", "", content.casefold())
        fingerprint = sha256(normalized.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append(
            {
                "candidate_id": str(uuid4()),
                "source": source,
                "fingerprint": fingerprint,
                "category": heading,
                "content": content,
                "evidence": content,
                "source_ref": Path(filename).name,
                "confidence": 0.9,
                "import_id": import_id,
                "created_at": now.isoformat(),
            }
        )
        if len(rows) >= 300:
            break
    if not rows:
        raise ValueError("Markdown contains no usable list items")
    return rows


def _usable_markdown_item(content: str) -> bool:
    normalized = content.strip().casefold()
    placeholders = {"", "n/a", "none", "unknown", "待填写", "未知", "无", "..."}
    return 3 <= len(content) <= 500 and normalized not in placeholders


def _heading_category(heading: str) -> str:
    if re.search(r"日程|时间|安排|schedule|calendar", heading, re.I):
        return "scheduling"
    if re.search(r"偏好|习惯|风格|preference|style|habit", heading, re.I):
        return "preference"
    if re.search(r"工作|项目|优先|work|project|priorit", heading, re.I):
        return "work"
    if re.search(r"身份|背景|关于|identity|background|about", heading, re.I):
        return "identity"
    if re.search(r"隐私|边界|禁|privacy|boundar", heading, re.I):
        return "boundary"
    if re.search(r"人物|关系|people|relationship", heading, re.I):
        return "relationship"
    if re.search(r"学习|研究|教育|learn|research|education", heading, re.I):
        return "learning"
    if re.search(r"设备|环境|系统|device|environment|system", heading, re.I):
        return "environment"
    if re.search(r"协作|沟通|合作|collaborat|communication", heading, re.I):
        return "collaboration"
    if re.search(r"兴趣|爱好|interest|hobby", heading, re.I):
        return "interest"
    return "personal"


def _personal_statements(text: str) -> list[str]:
    compact = re.sub(r"```.*?```", " ", text, flags=re.S)
    pieces = re.split(r"(?<=[。！？!?])|\n+", compact)
    pattern = re.compile(
        r"(?:我(?:是|叫|喜欢|偏好|通常|一般|习惯|希望|不喜欢|不想|需要|正在|目前|工作|居住|住在|每天|每周|倾向)|我的)"
        r"|(?:\b(?:i am|i'm|i prefer|i like|i dislike|i usually|i work|i need|i don't want|my\s+\w+\s+is)\b)",
        re.I,
    )
    results: list[str] = []
    for piece in pieces:
        statement = piece.strip(" \t\r\n-*•")
        if 4 <= len(statement) <= 500 and pattern.search(statement) and not statement.endswith(("?", "？")):
            results.append(statement)
    return results


def _category(text: str) -> str:
    if re.search(r"时间|日程|会议|上午|下午|晚上|每天|每周|schedule|meeting|morning|evening", text, re.I):
        return "scheduling"
    if re.search(r"喜欢|偏好|习惯|不喜欢|倾向|prefer|like|usually|dislike", text, re.I):
        return "preference"
    if re.search(r"项目|工作|公司|开发|研究|project|work|company|research", text, re.I):
        return "work"
    if re.search(r"叫|是|住在|居住|my name|i am|i'm", text, re.I):
        return "identity"
    return "personal"
