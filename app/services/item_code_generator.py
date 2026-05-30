class ItemCodeGeneratorService:
    @staticmethod
    def generate_fg_code(material_type: str, thickness: str, pattern_code: str, coating: str) -> str:
        mat = material_type.strip().upper()
        th = thickness.strip()
        pc = str(pattern_code).strip()
        coat = coating.strip().upper()
        return f"{mat}-{th}-{pc}-{coat}"
