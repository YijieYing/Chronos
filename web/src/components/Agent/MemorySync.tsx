import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  loadAgentContext,
  loadMemoryCandidates,
  loadMemoryImports,
  reviewMemoryCandidate,
  uploadMemoryDocument,
  type AgentContextItem,
  type MemoryCandidate,
  type MemoryImport,
  type MemorySource,
} from "../../api/agentMemory";
import styles from "./MemorySync.module.css";

const PROFILE_PROMPT = `请根据你目前对我的了解，生成一份可供 Chronos 导入的 Markdown 个人资料。

要求：
- 只输出 Markdown，不要解释。
- 每条信息必须是独立的列表项。
- 只写你有依据的信息；不确定的内容不要猜测。
- 不要包含密码、API Key、完整聊天原文或高度敏感信息。
- 尽量写长期稳定、会影响计划与协作方式的信息。
- 下面的分类只是建议；可以根据实际了解增加、删除或重命名章节。
- 可以使用嵌套列表表达一组相关信息。

# Personal Profile
## 身份与背景
- ...
## 工作与项目
- ...
## 偏好与习惯
- ...
## 日程与精力规律
- ...
## 当前优先事项
- ...
## 重要人物与关系
- ...
## 隐私边界
- ...`;

const IMPORT_EXTENSIONS = [".zip", ".md", ".markdown", ".txt"];

interface MemorySyncProps {
  open: boolean;
  onClose: () => void;
}

export function MemorySync({ open, onClose }: MemorySyncProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<MemorySource>("chatgpt");
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [imports, setImports] = useState<MemoryImport[]>([]);
  const [context, setContext] = useState<AgentContextItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [promptCopied, setPromptCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open]);

  async function refresh() {
    try {
      const [nextCandidates, nextImports, nextContext] = await Promise.all([
        loadMemoryCandidates(),
        loadMemoryImports(),
        loadAgentContext(),
      ]);
      setCandidates(nextCandidates);
      setImports(nextImports);
      setContext(nextContext);
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function importFile(file: File) {
    const lowerName = file.name.toLowerCase();
    if (!IMPORT_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
      setMessage("请选择 GPT 生成的 Markdown/TXT，或 ChatGPT/Claude 导出的 ZIP。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await uploadMemoryDocument(file, source);
      const successMessage =
        result.duplicate
          ? "这份文件已经导入过，没有重复生成候选。"
          : `扫描 ${result.messages_scanned} 条内容，新增 ${result.candidates_created} 条候选记忆。`;
      await refresh();
      setMessage(successMessage);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void importFile(file);
  }

  async function review(id: string, accepted: boolean) {
    try {
      await reviewMemoryCandidate(id, accepted);
      setCandidates((current) => current.filter((item) => item.candidate_id !== id));
      if (accepted) setContext(await loadAgentContext());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function copyProfilePrompt() {
    try {
      await navigator.clipboard.writeText(PROFILE_PROMPT);
      setPromptCopied(true);
      window.setTimeout(() => setPromptCopied(false), 1800);
    } catch {
      setMessage("无法访问剪贴板，请从 README 复制生成提示词。");
    }
  }

  if (!open) return null;
  return (
    <>
      <button className={styles.backdrop} aria-label="Close memory sync" onClick={onClose} />
      <aside className={styles.drawer}>
        <header>
          <div>
            <span>PERSONAL CONTEXT</span>
            <h2>Memory Sync</h2>
          </div>
          <button className={styles.close} onClick={onClose}>×</button>
        </header>

        <div className={styles.sourceTabs}>
          {(["chatgpt", "claude"] as const).map((item) => (
            <button
              key={item}
              data-active={source === item}
              onClick={() => setSource(item)}
            >
              {item}
            </button>
          ))}
        </div>

        <div className={styles.promptCard}>
          <div>
            <strong>QUICK PROFILE</strong>
            <p>把提示词发给 GPT/Claude，将回答保存为 .md 后拖入下方。</p>
          </div>
          <button onClick={() => void copyProfilePrompt()}>
            {promptCopied ? "COPIED" : "COPY GPT PROMPT"}
          </button>
        </div>

        <div
          className={styles.dropZone}
          data-dragging={dragging}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
        >
          <strong>{busy ? "IMPORTING…" : "DROP PROFILE MD OR EXPORT ZIP"}</strong>
          <p>原始文件仅保存在本机；候选需要逐条确认。</p>
          <input
            ref={inputRef}
            type="file"
            accept=".zip,.md,.markdown,.txt,application/zip,text/markdown,text/plain"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importFile(file);
              event.currentTarget.value = "";
            }}
          />
        </div>
        {message && <p className={styles.message}>{message}</p>}

        <section className={styles.section}>
          <div className={styles.sectionTitle}>
            <h3>Candidate memories</h3>
            <span>{candidates.length} pending</span>
          </div>
          {candidates.length === 0 ? (
            <p className={styles.empty}>没有待审核的候选记忆。</p>
          ) : candidates.map((candidate) => (
            <article className={styles.candidate} key={candidate.candidate_id}>
              <div className={styles.meta}>
                <span>{candidate.category}</span>
                <span>{candidate.source}</span>
              </div>
              <p>{candidate.content}</p>
              <small title={candidate.source_ref}>{candidate.source_ref}</small>
              <div className={styles.reviewActions}>
                <button onClick={() => void review(candidate.candidate_id, false)}>IGNORE</button>
                <button data-primary onClick={() => void review(candidate.candidate_id, true)}>ACCEPT</button>
              </div>
            </article>
          ))}
        </section>

        <section className={styles.section}>
          <div className={styles.sectionTitle}>
            <h3>Accepted context</h3>
            <span>{context.length} items</span>
          </div>
          {context.slice(0, 8).map((item) => (
            <p className={styles.contextItem} key={item.context_id}>{item.content}</p>
          ))}
        </section>

        <section className={styles.section}>
          <div className={styles.sectionTitle}>
            <h3>Import history</h3>
          </div>
          {imports.slice(0, 5).map((item) => (
            <div className={styles.importRow} key={item.import_id}>
              <span>{item.source}</span>
              <p>{item.archive_name}</p>
              <b>{item.candidates_created}</b>
            </div>
          ))}
        </section>
      </aside>
    </>
  );
}
