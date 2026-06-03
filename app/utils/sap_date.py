"""Parse SAP Service Layer date values (ISO or /Date(ms)/) for DB storage."""
import re
from datetime import date, datetime
from typing import Optional


def parse_sap_date(val) -> Optional[date]:
    """Return a date or None."""
    if val is None:
        return None
    if isinstance(val, str) and val.startswith('/Date('):
        m = re.search(r'/Date\((-?\d+)\)', val)
        if m:
            ms = int(m.group(1))
            return datetime.utcfromtimestamp(ms / 1000.0).date()
    if isinstance(val, str) and len(val) >= 10 and val[4] == '-':
        try:
            return datetime.strptime(val[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None
