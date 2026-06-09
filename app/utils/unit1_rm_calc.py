"""Unit 1: raw film kg from FG dispatch, width trim, and coating gsm (not % wastage)."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Union

PET_FILM_DENSITY = 1.38


def film_gsm_from_thickness_mic(thickness_mic: Union[float, int, None]) -> float:
    return max(0.0, float(thickness_mic or 0) * PET_FILM_DENSITY)


def parse_thickness_mic_from_item_code(code: str | None) -> Optional[float]:
    parts = (code or '').strip().upper().split('-')
    if len(parts) < 2:
        return None
    try:
        v = float(parts[1])
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def resolve_film_gsm(
    *,
    film_gsm: Union[float, int, None] = None,
    thickness_mic: Union[float, int, None] = None,
    rm_item_code: str | None = None,
) -> float:
    gsm = float(film_gsm) if film_gsm not in (None, '') else 0.0
    if gsm > 0:
        return gsm
    mic = float(thickness_mic) if thickness_mic not in (None, '') else 0.0
    if mic <= 0:
        parsed = parse_thickness_mic_from_item_code(rm_item_code)
        mic = float(parsed) if parsed else 0.0
    return film_gsm_from_thickness_mic(mic) if mic > 0 else 0.0


def compute_unit1_rm_plan(
    *,
    fg_kg: Union[float, int],
    fg_width_mm: Union[float, int],
    raw_width_mm: Union[float, int],
    film_gsm: Union[float, int, None] = None,
    thickness_mic: Union[float, int, None] = None,
    rm_item_code: str | None = None,
    chemical_gsm: Union[float, int] = 0,
    metallisation_gsm: Union[float, int] = 0,
) -> dict[str, Any]:
    """Backward from FG (coating included) to raw film kg and intermediate masses."""
    fg_k = max(0.0, float(fg_kg or 0))
    fg_w = max(0.0, float(fg_width_mm or 0))
    raw_w = max(0.0, float(raw_width_mm or 0))
    chem = max(0.0, float(chemical_gsm or 0))
    met = max(0.0, float(metallisation_gsm or 0))
    gsm = resolve_film_gsm(
        film_gsm=film_gsm,
        thickness_mic=thickness_mic,
        rm_item_code=rm_item_code,
    )
    gsm_total = gsm + chem + met
    ok = fg_k > 0 and fg_w > 0 and raw_w > 0 and gsm > 0 and gsm_total > 0

    if not ok:
        return {
            'ok': False,
            'raw_film_kg': fg_k,
            'after_embossing_kg': fg_k,
            'after_metallisation_kg': fg_k,
            'after_slitting_kg': fg_k,
            'fg_kg': fg_k,
            'wastage_kg': 0.0,
            'film_gsm': gsm,
        }

    raw_film_kg = fg_k * (raw_w / fg_w) * (gsm / gsm_total)
    after_emb_kg = raw_film_kg * (gsm + chem) / gsm
    after_met_kg = raw_film_kg * gsm_total / gsm
    after_slit_kg = after_met_kg * (fg_w / raw_w)
    width_trim_kg = raw_film_kg * max(0.0, 1.0 - (fg_w / raw_w)) if raw_w > 0 else 0.0

    return {
        'ok': True,
        'raw_film_kg': raw_film_kg,
        'after_embossing_kg': after_emb_kg,
        'after_metallisation_kg': after_met_kg,
        'after_slitting_kg': after_slit_kg,
        'fg_kg': fg_k,
        'wastage_kg': width_trim_kg,
        'film_gsm': gsm,
    }


def _is_embossing_title(title: str) -> bool:
    t = (title or '').strip().upper()
    return t == 'EMBOSSING' or 'EMB' in t


def _is_slitting_title(title: str) -> bool:
    t = (title or '').strip().upper()
    return t == 'SLITTING' or t.endswith('-SLT') or t == 'SLT'


def planned_kg_for_process_title(
    plan: dict[str, Any],
    process_title: str,
    conv_step_index: int = 0,
    sequence: Sequence[str] | None = None,
) -> float:
    """RM ≠ FG; embossing output flows until slitting; slitting/FG = order qty."""
    order_kg = float(plan.get('fg_kg') or 0)
    emb_out = float(plan.get('after_embossing_kg') or 0)
    raw_kg = float(plan.get('raw_film_kg') or 0)
    title = (process_title or '').strip().upper()
    seq = [str(s or '').strip() for s in (sequence or [])]

    if title in ('FG', 'PK-PACK', 'PK PACK') or 'PACK' in title:
        return order_kg
    if _is_slitting_title(title):
        return order_kg
    if _is_embossing_title(title):
        return emb_out

    emb_idx = next((i for i, s in enumerate(seq) if _is_embossing_title(s)), -1)
    slit_idx = next((i for i, s in enumerate(seq) if _is_slitting_title(s)), -1)
    if emb_idx >= 0 and conv_step_index > emb_idx and (slit_idx < 0 or conv_step_index < slit_idx):
        return emb_out
    return raw_kg


def aggregate_rm_plans(
    fg_lines: Sequence[tuple[float, float]],
    *,
    raw_width_mm: Union[float, int, None],
    film_gsm: Union[float, int, None] = None,
    thickness_mic: Union[float, int, None] = None,
    rm_item_code: str | None = None,
    chemical_gsm: Union[float, int] = 0,
    metallisation_gsm: Union[float, int] = 0,
) -> dict[str, Any]:
    per_line: list[dict[str, Any]] = []
    total_raw = 0.0
    total_waste = 0.0
    for fg_kg, fg_w in fg_lines:
        plan = compute_unit1_rm_plan(
            fg_kg=fg_kg,
            fg_width_mm=fg_w,
            raw_width_mm=raw_width_mm or 0,
            film_gsm=film_gsm,
            thickness_mic=thickness_mic,
            rm_item_code=rm_item_code,
            chemical_gsm=chemical_gsm,
            metallisation_gsm=metallisation_gsm,
        )
        per_line.append(plan)
        if plan.get('ok'):
            total_raw += float(plan['raw_film_kg'])
            total_waste += float(plan['wastage_kg'])
        else:
            total_raw += max(0.0, float(fg_kg))
    return {
        'ok': any(p.get('ok') for p in per_line),
        'per_line': per_line,
        'total_raw_film_kg': total_raw,
        'total_wastage_kg': total_waste,
        'total_rm_kg': total_raw,
    }


def ceil_kg(value: Union[float, int]) -> int:
    return max(0, int(math.ceil(float(value or 0))))
