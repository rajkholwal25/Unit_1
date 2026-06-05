"""Unit 1 item codes and human-readable descriptions (pattern name + process labels)."""

from __future__ import annotations

import re

from app.models.reference import ProcessMaster
from app.services.unit1_processes import UNIT1_PROCESS_CODE_SUFFIXES, unit1_fg_base_code
from app.utils.thickness import parse_thickness, thickness_display
from app.utils.unit1_units import THICKNESS_SUFFIX


def pattern_segment_for_display(pattern_name: str) -> str:
    """
    Pattern segment for ItemName / descriptions, e.g. ``Rectangle`` → ``Rectangle``,
    ``Round Hole`` → ``Round-Hole``.
    """
    name = (pattern_name or '').strip()
    if not name:
        raise ValueError('pattern name required')
    segment = re.sub(r'[^\w\s-]', '', name, flags=re.UNICODE)
    segment = re.sub(r'\s+', '-', segment.strip())
    segment = re.sub(r'-+', '-', segment)
    if not segment:
        raise ValueError('pattern name has no valid characters')
    return segment[:40]


def unit1_fg_human_label(
    material_type: str,
    thickness,
    pattern_name: str,
    coating: str,
) -> str:
    """Human FG label for UI/SAP ItemName: ``PET 12MIC Triangle TR`` (pattern **name**, not code)."""
    mat = (material_type or '').strip().upper()
    th = thickness_display(parse_thickness(thickness) if thickness is not None else thickness)
    pn = (pattern_name or '').strip()
    coat = (coating or '').strip().upper()
    if not mat or th == '—' or not pn or not coat:
        return ''
    return f'{mat} {th}{THICKNESS_SUFFIX} {pn} {coat}'[:128]


def unit1_fg_human_label_from_item_code(
    item_code: str,
    *,
    pattern_name: str | None = None,
) -> str:
    """``PET-12-1009-TR`` → ``PET 12MIC Triangle TR`` when pattern 1009 = Triangle."""
    from ..models import Pattern

    base = unit1_fg_base_code((item_code or '').strip())
    if not base:
        return ''
    parts = base.split('-')
    if len(parts) < 4:
        return ''
    mat, th, pat_code, coat = parts[0], parts[1], parts[2], parts[3]
    pn = (pattern_name or '').strip()
    if not pn:
        row = Pattern.query.filter_by(pattern_code=pat_code).first()
        pn = (row.pattern_name if row else pat_code).strip()
    return unit1_fg_human_label(mat, th, pn, coat)


def unit1_fg_display_name(
    material_type: str,
    thickness,
    pattern_name: str,
    coating: str,
) -> str:
    return unit1_fg_human_label(material_type, thickness, pattern_name, coating)


def unit1_fg_display_name_from_item_code(
    item_code: str,
    *,
    pattern_name: str | None = None,
) -> str:
    return unit1_fg_human_label_from_item_code(item_code, pattern_name=pattern_name)


def resolve_fg_display_name(payload: dict, pattern=None) -> str:
    """FG label: derive from ``fg_code`` (pattern name + micron) when possible; else explicit / generator fields."""
    explicit = (payload.get('fg_name') or '').strip()
    fg_code = (payload.get('fg_code') or '').strip()
    if payload.get('prefer_fg_name') and explicit:
        return explicit[:128]
    if fg_code:
        pn = pattern.pattern_name if pattern else None
        derived = unit1_fg_human_label_from_item_code(fg_code, pattern_name=pn)
        if derived:
            return derived
    if explicit and explicit != fg_code:
        return explicit
    if pattern is None and payload.get('pattern_id'):
        from ..models import Pattern

        pattern = Pattern.query.get(payload['pattern_id'])
    material = payload.get('material_type')
    thickness = payload.get('thickness')
    coating = payload.get('coating')
    if pattern and material and thickness is not None and coating:
        return unit1_fg_human_label(material, thickness, pattern.pattern_name, coating)
    return explicit or fg_code or ''


def resolve_fg_name_for_snap(fg_code: str, fg_name_hint: str = '') -> str:
    """Name stored on job header lines: pattern name label, not SAP abbrev or pattern code."""
    code = (fg_code or '').strip()
    if code:
        lbl = unit1_fg_human_label_from_item_code(code)
        if lbl:
            return lbl
    hint = (fg_name_hint or '').strip()
    return hint or code


def build_process_label_map() -> dict[str, str]:
    """Map uppercased process tail / full ``process_code`` → ``process_master.name``."""
    exact: dict[str, str] = {}
    suffix_pairs: dict[str, list[tuple[str, str]]] = {}

    for row in ProcessMaster.query.filter_by(is_active=True).order_by(
        ProcessMaster.process_code
    ).all():
        code_u = (row.process_code or '').strip().upper()
        nm = (row.name or '').strip()
        if not code_u or not nm:
            continue
        exact[code_u] = nm
        parts = code_u.split('-')
        suff = parts[-1] if parts else ''
        if len(suff) >= 2:
            suffix_pairs.setdefault(suff, []).append((code_u, nm))

    out = dict(exact)
    for suff, pairs in suffix_pairs.items():
        names = {p[1] for p in pairs}
        if suff in out:
            continue
        if len(names) == 1:
            out[suff] = next(iter(names))
        else:
            code_nm = min(pairs, key=lambda p: len(p[0]))
            out[suff] = code_nm[1]
    return out


def unit1_process_label_for_tail(tail: str, label_map: dict[str, str] | None = None) -> str:
    key = (tail or '').strip().upper().replace(' ', '')
    if not key:
        return ''
    m = label_map if label_map is not None else build_process_label_map()
    return m.get(key) or m.get(key.split('-')[-1]) or ''


def unit1_process_tail_from_code(item_code: str) -> str:
    code = (item_code or '').strip()
    if not code:
        return ''
    base = unit1_fg_base_code(code)
    upper = code.upper()
    base_u = base.upper()
    if upper == base_u:
        return ''
    if upper.startswith(base_u + '-'):
        return code[len(base) + 1 :]
    parts = code.split('-')
    if parts and parts[-1].upper() in UNIT1_PROCESS_CODE_SUFFIXES:
        return parts[-1]
    return parts[-1] if parts else ''


def unit1_process_item_description(
    item_code: str,
    *,
    pattern_name: str | None = None,
    label_map: dict[str, str] | None = None,
) -> str:
    """
    SAP ItemName for a process output: display code (pattern **name**) + process label.
    ItemCode ``PET-12-1009-TR-EMB`` → ``PET-12-Rectangle-TR-EMB Embossing``.
    """
    code = (item_code or '').strip()
    if not code:
        return ''
    tail = unit1_process_tail_from_code(code)
    display_base = unit1_fg_human_label_from_item_code(
        unit1_fg_base_code(code),
        pattern_name=pattern_name,
    )
    if not tail:
        return display_base[:128]
    label = unit1_process_label_for_tail(tail, label_map)
    if not label:
        label = tail.replace('-', ' ').title()
    return f'{display_base} {label}'.strip()[:128]
