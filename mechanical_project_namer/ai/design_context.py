# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Deterministic local retrieval and bounded extractive history. No extra API calls."""
import re

MAX_CONTEXT_CHARS = 24000
MAX_QUESTION_CHARS = 12000
MAX_MEMORY_CHARS = 2000


def compact(text: str) -> str:
    return " ".join(str(text).split())


def excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 12)] + "【节选，原文保留】" if limit >= 12 else text[:limit]


def terms(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9]+", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return result - {"什么", "怎么", "如何", "需要", "可以", "一下", "请问", "问题", "这个", "那个", "我们", "我的", "是否", "注意"}


def relevance(query: str, text: str) -> int:
    query_terms = terms(query)
    return len(query_terms & terms(text)) if query_terms else 0


def memory_rank_sql(query: str, columns: tuple[str, ...]) -> tuple[str, list[str]]:
    # Bound SQL size; rank in SQLite so old memories are not excluded by recency.
    keywords = sorted(terms(query), key=lambda t: (-len(t), t))[:24]
    if not keywords:
        return "0", []
    text = " || ' ' || ".join(f"lower({c})" for c in columns)
    return " + ".join(f"CASE WHEN instr({text}, ?) > 0 THEN 1 ELSE 0 END" for _ in keywords), keywords


def explicit_preference(text: str) -> str | None:
    # A deliberately explicit command, not inference from incidental project values.
    match = re.fullmatch(r"\s*(?:请)?记住我的(?:习惯|偏好)[：:]\s*(.+?)\s*", text, re.S)
    return match.group(1).strip() if match else None


def summarize_history(messages: list[dict], limit: int = 4000) -> str:
    """Preserve early user constraints, newest changes, and unconfirmed AI separately.

    Rebuilt from stored originals, so repeated shortening never compounds loss.
    Only an extract: it does not infer or certify engineering facts.
    """
    if not messages or limit <= 0:
        return ""
    user = [m for m in messages if m["role"] == "user"]
    changes = [m for m in user if re.search(r"改为|改成|更正|纠正|取消|不再|采用|确定|确认|最终|选择|待定|尚未|还没|未确定", m["content"])]
    assistants = [m for m in messages if m["role"] == "assistant"]
    seen = set()
    sections = []
    for title, selected, share in (
        ("最新用户修正、选择与待办（摘录，新信息优先）", list(reversed(changes)), .4),
        ("初始用户目标与条件（摘录，可能已被后续修正）", user[:4], .3),
        ("其余近期用户信息（摘录）", list(reversed(user)), .2),
        ("AI历史建议（未自动确认或采用）", list(reversed(assistants)), .1),
    ):
        budget = max(0, int((limit - 160) * share))
        lines = []
        for m in selected:
            identity = m["message_id"]
            if identity in seen:
                continue
            label = f"[消息{identity}] "
            line = label + excerpt(compact(m["content"]), min(600, max(0, budget - len(label) - 1)))
            if budget < len(label) + 24:
                break
            lines.append(line)
            seen.add(identity)
            budget -= len(line) + 1
        if lines:
            sections.append(title + "：\n" + "\n".join(lines))
    return excerpt("\n\n".join(sections), limit)


def bound_messages(messages: list[dict], maximum: int = MAX_CONTEXT_CHARS) -> tuple[list[dict], bool]:
    """Keep workflow and current question intact; retain recent complete turns."""
    if len(messages[-1]["content"]) > MAX_QUESTION_CHARS:
        raise ValueError("本次问题超过12000字，请分段发送；原输入已保留。")
    fixed = [dict(messages[0]), dict(messages[-1])]
    remaining = maximum - sum(len(m["content"]) for m in fixed)
    if remaining < 0:
        raise ValueError("上下文超出限额，请缩短本次输入；原输入已保留。")
    selected = []
    reduced = False
    middle = messages[1:-1]
    # Summary (role system) is reserved before choosing newest chat turns.
    summaries = [m for m in middle if m["role"] == "system"]
    for m in summaries:
        size = min(len(m["content"]), remaining // 3, 4000)
        if size:
            selected.append({**m, "content": excerpt(m["content"], size)})
            remaining -= len(selected[-1]["content"])
        reduced |= size < len(m["content"])
    history = [m for m in middle if m["role"] != "system"]
    turns = []
    for m in history:
        if not turns and m['role'] != 'user':
            reduced = True
            continue
        if m["role"] == "user" or not turns:
            turns.append([])
        turns[-1].append(m)
    kept = []
    for turn in reversed(turns):
        required = sum(len(m["content"]) for m in turn)
        if required <= remaining:
            kept[:0] = [dict(m) for m in turn]
            remaining -= required
        else:
            # Keep the latest turn as labelled excerpts, never orphan an answer.
            if not kept and remaining >= 300 * len(turn):
                allowance = remaining // len(turn)
                kept[:0] = [{**m, "content": excerpt(m["content"], allowance)} for m in turn]
            reduced = True
            break
    result = [fixed[0], *selected, *kept, fixed[1]]
    assert sum(len(m["content"]) for m in result) <= maximum
    return result, reduced
