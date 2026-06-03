from __future__ import annotations

from typing import Iterable, List, Optional


def ordered_unique_codes(values: Optional[Iterable[object]]) -> List[str]:
    """Return trimmed codes in first-seen order, comparing case-insensitively."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        code = str(value or '').strip()
        if not code:
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(code)
    return out


def merge_ordered_unique_codes(
    primary_values: Optional[Iterable[object]],
    secondary_values: Optional[Iterable[object]],
) -> List[str]:
    """Merge two ordered code lists while preserving primary order.

    The primary list wins for ordering, but any codes missing from it are inserted
    using the relative order from the secondary list. This keeps planner-only
    codes such as outsourcing in place while backfilling missing real BOM steps.
    """
    primary = ordered_unique_codes(primary_values)
    secondary = ordered_unique_codes(secondary_values)
    if not primary:
        return secondary
    if not secondary:
        return primary

    result: list[str] = list(primary)
    result_keys: set[str] = {code.upper() for code in result}
    secondary_keys: list[str] = [code.upper() for code in secondary]

    def result_index(key: str) -> int:
        key_u = key.upper()
        for idx, existing in enumerate(result):
            if existing.upper() == key_u:
                return idx
        return -1

    for sec_idx, code in enumerate(secondary):
        key = code.upper()
        if key in result_keys:
            continue

        insert_at = len(result)
        for later_key in secondary_keys[sec_idx + 1:]:
            if later_key in result_keys:
                later_idx = result_index(later_key)
                if later_idx >= 0:
                    insert_at = later_idx
                break

        result.insert(insert_at, code)
        result_keys.add(key)

    return result
