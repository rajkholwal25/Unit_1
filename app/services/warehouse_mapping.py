"""Unit 1 warehouse codes per process (FBD-*). Used by BOM builder and manufacturing jobs."""


class WarehouseMappingService:
    MAP = {
        'EMB': 'FBD-EMB',
        'MET': 'FBD-MTL',
        'MTL': 'FBD-MTL',
        'SLT': 'FBD-SLT',
        'HRI': 'FBD-HRI',
        'COAT': 'FBD-COAT',
        'ALO': 'FBD-COAT',
        'ALOX': 'FBD-ALOX',
        'MAT': 'FBD-COAT',
        'FG': 'FBD-FG',
        'RM': 'FBD-RM',
        'PK-PACK': 'FBD-FG',
    }

    @classmethod
    def for_process(cls, process_code: str) -> str:
        key = (process_code or '').strip().upper()
        if not key:
            return cls.MAP['RM']
        if key in cls.MAP:
            return cls.MAP[key]
        tail = key.split('-')[-1] if '-' in key else key
        return cls.MAP.get(tail, cls.MAP['RM'])

    @classmethod
    def default_po_warehouse(cls) -> str:
        return cls.MAP['FG']
