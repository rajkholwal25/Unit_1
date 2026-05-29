class WarehouseMappingService:
    MAP = {
        'EMB':'FBD-EMB',
        'MET':'FBD-MTL',
        'SLT':'FBD-SLT',
        'HRI':'FBD-HRI',
        'COAT':'FBD-COAT',
        'ALOX':'FBD-ALOX',
        'FG':'FBD-FG',
        'RM':'FBD-RM'
    }

    @classmethod
    def for_process(cls, process_code: str) -> str:
        return cls.MAP.get(process_code.upper(), 'FBD-RM')
