// Project memory bank (design §17.3 P14) — the console's projection of
// `ih memory`. The engine injects this bank into every runtime prompt; before
// this page the operator could only see it from the REPL's `/memory` command.
// Read-only inventory plus an injection preview built by the SAME code path as
// the prompt, and the two write operations (add/overwrite, delete) an operator
// reasonably wants from a console. Every write requires a confirmation dialog
// and is audited server-side.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useWebUi } from "../store";
import {
  formatModified,
  humanBytes,
  memoryHeadline,
  memorySlug,
  visibleEntries,
} from "../memory";
import type {
  MemoryEntry,
  MemoryEntryDetail,
  MemoryView,
} from "../types";

function copyText(text: string, onDone: (ok: boolean) => void): void {
  if (!navigator.clipboard?.writeText) {
    onDone(false);
    return;
  }
  void navigator.clipboard
    .writeText(text)
    .then(() => onDone(true))
    .catch(() => onDone(false));
}

function CopyButton({
  text,
  label = "复制",
}: {
  text: string;
  label?: string;
}): React.JSX.Element {
  const pushToast = useWebUi((state) => state.pushToast);
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn small"
      type="button"
      onClick={() =>
        copyText(text, (ok) => {
          if (!ok) {
            pushToast("error", "无法写入剪贴板，请手动选择文本");
            return;
          }
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        })
      }
      disabled={!text}
    >
      {copied ? "已复制" : label}
    </button>
  );
}

function ProblemRow({ problem }: { problem: { name: string; detail: string; remedy: string } }): React.JSX.Element {
  return (
    <tr className="row-warn">
      <td className="mono strong">{problem.name}</td>
      <td>{problem.detail}</td>
      <td>
        {problem.remedy ? (
          <div className="remedy-row">
            <code className="remedy">{problem.remedy}</code>
            <CopyButton text={problem.remedy} label="复制修复方法" />
          </div>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

function InjectionBlock({ view }: { view: MemoryView }): React.JSX.Element {
  const injection = view.injection;
  if (!injection) return <></>;
  if (!injection.enabled) {
    return (
      <section className="panel">
        <h2>注入预览</h2>
        <p className="empty">记忆注入当前禁用 — {injection.reason || "memory.enabled = false"}</p>
      </section>
    );
  }
  return (
    <section className="panel">
      <h2>注入预览</h2>
      <p className="muted">
        对「{injection.prompt}」而言，运行时提示词会注入 {injection.chars} 字符、
        {injection.files}/{injection.max_files} 个相关记忆文件，
        索引 {injection.entrypoint_lines_included}/{injection.entrypoint_lines_total} 行
        {injection.entrypoint_truncated ? "（索引已截断）" : ""}。
      </p>
      {injection.truncated_files.length > 0 && (
        <p className="muted warn-text">
          以下记忆被每文件 {injection.max_entry_chars} 字符预算截断：
          {injection.truncated_files
            .map((f) => `${f.name}（${f.chars_included}/${f.chars_total}）`)
            .join("、")}
          。模型只看到截断后的内容。
        </p>
      )}
      {injection.rendered ? (
        <>
          <pre className="memory-preview mono">{injection.rendered}</pre>
          <CopyButton text={injection.rendered} label="复制注入文本" />
        </>
      ) : (
        <p className="empty">没有记忆会被注入（无相关文件）。</p>
      )}
    </section>
  );
}

export default function Memory(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const pushToast = useWebUi((state) => state.pushToast);
  const [view, setView] = useState<MemoryView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [prompt, setPrompt] = useState("");
  const [previewPrompt, setPreviewPrompt] = useState("");
  const [detail, setDetail] = useState<MemoryEntryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<MemoryEntry | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [addTitle, setAddTitle] = useState("");
  const [addContent, setAddContent] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(
    async (opts: { query?: string; prompt?: string } = {}): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        setView(
          await api.memory(projectRoot, {
            query: opts.query ?? query,
            prompt: opts.prompt ?? prompt,
          }),
        );
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : String(caught);
        setError(message);
        pushToast("error", `读取记忆失败：${message}`);
      } finally {
        setLoading(false);
      }
    },
    [projectRoot, query, prompt, pushToast],
  );

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectRoot]);

  const runSearch = (): void => {
    setPrompt("");
    void load({ query, prompt: "" });
  };

  const runPreview = (): void => {
    void load({ prompt: previewPrompt });
  };

  const openDetail = async (name: string): Promise<void> => {
    setDetailLoading(true);
    setDetail(null);
    try {
      setDetail(await api.memoryEntry(name, projectRoot));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      pushToast("error", `读取记忆内容失败：${message}`);
    } finally {
      setDetailLoading(false);
    }
  };

  const confirmDelete = async (): Promise<void> => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      const result = await api.deleteMemory(pendingDelete.name, projectRoot);
      pushToast("success", result.message || `已删除 ${pendingDelete.name}`);
      setPendingDelete(null);
      setDetail(null);
      await load({});
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      pushToast("error", `删除失败：${message}`);
    } finally {
      setDeleting(false);
    }
  };

  const confirmAdd = async (): Promise<void> => {
    if (!addTitle.trim() || !addContent.trim()) {
      pushToast("error", "请填写标题与内容");
      return;
    }
    setAdding(true);
    try {
      const existing = (view?.entries ?? []).find(
        (entry) => entry.name === memorySlug(addTitle),
      );
      if (existing && !confirm(`记忆 ${existing.name} 已存在，覆盖它将无法撤销。确定覆盖？`)) {
        setAdding(false);
        return;
      }
      const result = await api.createMemory(
        { title: addTitle.trim(), content: addContent, overwrite: Boolean(existing) },
        projectRoot,
      );
      pushToast("success", result.message || "已保存记忆");
      setAddTitle("");
      setAddContent("");
      setShowAdd(false);
      await load({});
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      pushToast("error", `保存失败：${message}`);
    } finally {
      setAdding(false);
    }
  };

  const entries = view ? visibleEntries(view) : [];
  const summary = view?.summary ?? {};
  const headline = view ? memoryHeadline(view) : { label: "—", tone: "ok" as const, detail: "" };
  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">记忆</h1>
        <p className="page-sub">
          本项目持久化记忆库（与 `ih memory` 同源）——引擎注入每次运行提示词的内容
        </p>
      </div>
      <button className="btn" onClick={() => void load({})} disabled={loading}>
        {loading ? "读取中…" : "重新读取"}
      </button>
    </div>
  );

  if (loading && !view) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 加载记忆…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取记忆失败：{error}</p>
          <button className="btn primary" onClick={() => void load({})}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!view) return <>{header}</>;

  return (
    <>
      {header}
      {error && <p className="empty">刷新失败（显示上次结果）：{error}</p>}

      <div className="cards">
        <div className="card">
          <div className="k">状态</div>
          <div className={`v small direction-${headline.tone === "ok" ? "ok" : "warn"}`}>
            {headline.label}
          </div>
          <div className="muted card-note">{headline.detail}</div>
        </div>
        <div className="card">
          <div className="k">注入</div>
          <div className={`v small direction-${summary.enabled ? "ok" : "warn"}`}>
            {summary.enabled ? "已启用" : "已禁用"}
          </div>
          <div className="muted card-note">
            {summary.enabled
              ? `最多 ${String(summary.max_files)} 个相关记忆 / 索引 ${String(summary.max_entrypoint_lines)} 行`
              : "settings.memory.enabled = false"}
          </div>
        </div>
        <div className="card">
          <div className="k">记忆文件</div>
          <div className="v small mono">{String(summary.entries ?? 0)}</div>
          <div className="muted card-note">目录{view.exists ? "" : "尚未创建"}</div>
        </div>
        <div className="card">
          <div className="k">总大小</div>
          <div className="v small mono">{humanBytes(Number(summary.bytes ?? 0))}</div>
          <div className="muted card-note">全部记忆文件</div>
        </div>
        <div className="card">
          <div className="k">索引</div>
          <div className="v small mono">
            {String(summary.index_lines ?? 0)} 行
            {Number(summary.missing_index_targets ?? 0) > 0
              ? ` / ${String(summary.missing_index_targets)} 个失效链接`
              : ""}
          </div>
          <div className="muted card-note">MEMORY.md（引擎读它做索引）</div>
        </div>
      </div>

      {view.problems.length > 0 && (
        <section className="panel">
          <h2>需要处理（{view.problems.length}）</h2>
          <p className="muted">
            这些记忆文件不可读，或 MEMORY.md 链接了一个已不存在的文件——模型会在提示词里读到断链。
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>问题</th>
                  <th>修复方法</th>
                </tr>
              </thead>
              <tbody>
                {view.problems.map((problem) => (
                  <ProblemRow key={`${problem.name}:${problem.detail}`} problem={problem} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="filter-row memory-filters">
        <input
          type="search"
          placeholder="按相关性搜索记忆（回车执行）…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              runSearch();
            }
          }}
          aria-label="搜索记忆"
        />
        <button className="btn small" onClick={runSearch} disabled={loading}>
          搜索
        </button>
        <span className="muted">
          {view.query
            ? `匹配 ${view.matches.length} 条（按引擎相关度排序）`
            : `共 ${view.entries.length} 条`}
        </span>
        <span className="spacer" />
        <button className="btn small primary" onClick={() => setShowAdd(true)}>
          + 新增记忆
        </button>
      </div>

      <section className="panel">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>文件</th>
                <th>类型</th>
                <th>大小</th>
                <th>修改时间</th>
                <th>描述</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty">
                    {view.exists
                      ? view.query
                        ? "没有记忆匹配此搜索。"
                        : "还没有记忆文件。点击「+ 新增记忆」记录一条项目经验，或运行 `ih memory add \"标题\" --content \"…\"`。"
                      : "记忆目录尚未创建——第一条记忆会自动创建它。"}
                  </td>
                </tr>
              )}
              {entries.map((entry) => (
                <tr key={entry.name}>
                  <td className="mono strong">{entry.name}</td>
                  <td>{entry.type ? <span className="badge neutral">{entry.type}</span> : <span className="muted">—</span>}</td>
                  <td className="mono">{humanBytes(entry.bytes)}</td>
                  <td className="mono">{formatModified(entry.modified_at)}</td>
                  <td className="memory-desc">{entry.description || <span className="muted">—</span>}</td>
                  <td className="row-actions">
                    <button
                      className="btn small"
                      type="button"
                      onClick={() => void openDetail(entry.name)}
                      disabled={detailLoading && detail?.name !== entry.name}
                    >
                      查看
                    </button>
                    <button
                      className="btn small danger"
                      type="button"
                      onClick={() => setPendingDelete(entry)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>注入预览</h2>
        <p className="muted">
          输入一个任务，查看引擎对此会注入哪些记忆（与运行时提示词同一条代码路径）。
        </p>
        <div className="filter-row memory-filters">
          <input
            type="search"
            placeholder="例如：为什么测试在 CI 上不稳定"
            value={previewPrompt}
            onChange={(event) => setPreviewPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                runPreview();
              }
            }}
            aria-label="预览注入的任务文本"
          />
          <button className="btn small" onClick={runPreview} disabled={loading || !previewPrompt.trim()}>
            预览注入
          </button>
        </div>
        <InjectionBlock view={view} />
      </section>

      {detail && (
        <section className="panel">
          <div className="page-head">
            <div>
              <h2>{detail.name}</h2>
              <p className="muted">
                {humanBytes(detail.bytes)} · 修改于 {formatModified(detail.modified_at)}
                {detail.truncated ? "（内容过长，已截断显示）" : ""}
              </p>
            </div>
            <div className="row-actions">
              <CopyButton text={detail.path} label="复制路径" />
              <button className="btn small" onClick={() => setDetail(null)}>
                关闭
              </button>
            </div>
          </div>
          <pre className="memory-content mono">{detail.content}</pre>
        </section>
      )}

      {showAdd && (
        <ConfirmDialog
          title="新增记忆"
          confirmLabel={adding ? "保存中…" : "保存"}
          busy={adding}
          onCancel={() => setShowAdd(false)}
          onConfirm={() => void confirmAdd()}
        >
          <label className="field-label">
            标题
            <input
              value={addTitle}
              onChange={(event) => setAddTitle(event.target.value)}
              placeholder="例如：pytest quirks"
              autoFocus
            />
          </label>
          <label className="field-label">
            内容（将注入未来每次运行的提示词）
            <textarea
              className="memory-add-content"
              value={addContent}
              onChange={(event) => setAddContent(event.target.value)}
              placeholder="例如：fixtures are slow; use -x for fast feedback"
              rows={6}
            />
          </label>
        </ConfirmDialog>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="删除记忆"
          confirmLabel={deleting ? "删除中…" : "删除"}
          danger
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setPendingDelete(null)}
        >
          确定删除「{pendingDelete.name}」并从 MEMORY.md 移除其链接？此操作不可撤销。
        </ConfirmDialog>
      )}
    </>
  );
}
