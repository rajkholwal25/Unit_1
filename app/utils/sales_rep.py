"""Sales employee code → display name for print slips (company mapping)."""
from __future__ import annotations

from typing import Dict, Optional, Union

# Maps SalesPersonCode / SlpCode from ORDR (OSLP) to the name shown on slip 1.
SALES_REP_NAMES_BY_CODE: Dict[int, str] = {
    -1: "-No Sales Employee-",
    1: "Namit Jain",
    2: "Shivam Kanwar",
    3: "Ranjeev Duggal",
    4: "Amneet Gill",
    5: "Ravi Tripathi",
    6: "Unit 1",
    7: "Internal",
}


def resolve_sales_rep_display_name(code: Optional[Union[int, str]]) -> Optional[str]:
    """Return the mapped display name for a sales employee code, or None if unknown."""
    if code in (None, ""):
        return None
    try:
        i = int(str(code).strip())
    except (TypeError, ValueError):
        return None
    return SALES_REP_NAMES_BY_CODE.get(i)
