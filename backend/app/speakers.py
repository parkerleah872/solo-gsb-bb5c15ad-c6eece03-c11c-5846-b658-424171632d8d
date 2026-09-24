"""说话人归一（speaker normalization）引擎。

工作台汇总项目**已保存**数据中的现有说话人名称：

- 转写片段 ``document.segments[].speaker``；
- 校注 ``document.annotations[].speaker``；
- 规则的**精确说话人条件** ``annotation_rules.speaker``（规则说话人为空
  表示「不限」，不作为现有名称）。

用户配置若干条「旧名 → 目标名」映射。映射支持**链式归并**：``A → B``
与 ``B → C`` 会把 ``A`` 与 ``B`` 一并归一到最终名称 ``C``。提交前必须
通过两条校验，否则阻止提交：

1. **空目标**：某条映射的目标名为空 / 纯空白；
2. **循环映射**：沿映射边能走回自身（如 ``A → B``、``B → A``）。

校验与归并结果同时用于确认前预览（每个名称的最终去向、受影响的片段 /
校注 / 规则数量）与一次性事务写入。
"""
from __future__ import annotations

from typing import Any


def _clean(name: Any) -> str:
    """Speaker names compare verbatim; mappings are only whitespace-trimmed."""
    return str(name or "").strip()


def collect_speakers(document: dict, rule_rows: list[dict]) -> dict:
    """Aggregate the existing speaker names from all three sources.

    Returns ``{name: {"segments", "annotations", "rules"}}`` with the number
    of records carrying the name in each source.  Empty speakers and rules
    with an empty ("any speaker") condition are excluded.
    """
    stats: dict[str, dict[str, int]] = {}

    def bump(name: str, key: str) -> None:
        name = _clean(name)
        if not name:
            return
        bucket = stats.setdefault(
            name, {"segments": 0, "annotations": 0, "rules": 0})
        bucket[key] += 1

    for seg in (document or {}).get("segments", []):
        if isinstance(seg, dict):
            bump(seg.get("speaker"), "segments")
    for ann in (document or {}).get("annotations", []):
        if isinstance(ann, dict):
            bump(ann.get("speaker"), "annotations")
    for rule in rule_rows or []:
        bump(rule.get("speaker"), "rules")
    return stats


def normalize_mappings(raw: list[dict]) -> dict[str, str]:
    """Turn the API mapping rows into a {oldName: targetName} dict.

    Rows with a blank source are ignored (not yet filled in the UI).
    Duplicate sources with different targets are rejected.
    """
    mappings: dict[str, str] = {}
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        source = _clean(row.get("from"))
        target = _clean(row.get("to"))
        if not source:
            continue
        if source in mappings and mappings[source] != target:
            raise ValueError(f"旧名「{source}」配置了多个不同的目标名")
        mappings[source] = target
    return mappings


def validate_mappings(mappings: dict[str, str]) -> None:
    """Reject empty targets and mapping cycles; raise ValueError otherwise."""
    for source, target in mappings.items():
        if not target:
            raise ValueError(f"旧名「{source}」的目标名为空，请填写或删除该映射")

    for start in mappings:
        seen: list[str] = []
        current = start
        while current in mappings and mappings[current] != current:
            if current in seen:
                cycle = " → ".join(seen[seen.index(current):] + [current])
                raise ValueError(f"存在循环映射：{cycle}")
            seen.append(current)
            current = mappings[current]


def resolve_name(name: str, mappings: dict[str, str]) -> tuple[str, list[str]]:
    """Follow the mapping chain to the final name.

    Returns ``(finalName, chain)`` where ``chain`` is the sequence of names
    visited starting from ``name`` (``[name, …, finalName]``).  Names
    without a mapping resolve to themselves.
    """
    chain = [name]
    current = name
    while current in mappings and mappings[current] != current:
        current = mappings[current]
        chain.append(current)
    return current, chain


def build_preview(document: dict, rule_rows: list[dict],
                  raw_mappings: list[dict]) -> dict:
    """Compute the confirmation preview for the given mappings.

    Every existing name gets an entry with its final destination and the
    affected record counts; ``targets`` groups the same information by final
    name.  Mapping rows whose source does not exist anywhere are reported
    separately so the UI can warn they have no effect.
    """
    mappings = normalize_mappings(raw_mappings)
    validate_mappings(mappings)

    existing = collect_speakers(document, rule_rows)

    names = []
    targets: dict[str, dict] = {}
    changed_names = 0
    for name in sorted(existing):
        final, chain = resolve_name(name, mappings)
        counts = existing[name]
        changed = final != name
        if changed:
            changed_names += 1
        entry = {
            "name": name,
            "target": final,
            "changed": changed,
            "chain": chain,
            "segments": counts["segments"],
            "annotations": counts["annotations"],
            "rules": counts["rules"],
        }
        names.append(entry)
        group = targets.setdefault(final, {
            "name": final,
            "segments": 0, "annotations": 0, "rules": 0,
            "sourceNames": [],
        })
        group["segments"] += counts["segments"]
        group["annotations"] += counts["annotations"]
        group["rules"] += counts["rules"]
        if changed:
            group["sourceNames"].append(name)

    unused = [s for s in sorted(mappings) if s not in existing]

    def changed_records(key: str) -> int:
        return sum(n[key] for n in names if n["changed"])

    return {
        "names": names,
        "targets": [targets[t] for t in sorted(targets)],
        "unusedMappings": [{"from": s, "to": mappings[s]} for s in unused],
        "stats": {
            "nameCount": len(names),
            "changedNameCount": changed_names,
            "changedSegments": changed_records("segments"),
            "changedAnnotations": changed_records("annotations"),
            "changedRules": changed_records("rules"),
            "mappingCount": len(mappings),
            "unusedMappingCount": len(unused),
        },
    }


def apply_mappings(document: dict, rules: list[dict],
                   mappings: dict[str, str]) -> dict:
    """Rewrite the speaker fields in the three sources in place.

    Only the speaker fields change; record ids and every other field are
    preserved.  Returns counts of the records actually changed.
    """
    def rename(item: dict, key: str) -> bool:
        current = item.get(key)
        if not isinstance(current, str) or not current.strip():
            return False
        final, _ = resolve_name(current, mappings)
        if final == current:
            return False
        item[key] = final
        return True

    changed_segments = 0
    for seg in (document or {}).get("segments", []):
        if isinstance(seg, dict):
            changed_segments += 1 if rename(seg, "speaker") else 0
    changed_annotations = 0
    for ann in (document or {}).get("annotations", []):
        if isinstance(ann, dict):
            changed_annotations += 1 if rename(ann, "speaker") else 0
    changed_rules = 0
    for rule in rules or []:
        speaker = rule.get("speaker")
        if isinstance(speaker, str) and speaker.strip():
            final, _ = resolve_name(speaker, mappings)
            if final != speaker:
                rule["speaker"] = final
                changed_rules += 1
    return {
        "segments": changed_segments,
        "annotations": changed_annotations,
        "rules": changed_rules,
    }
