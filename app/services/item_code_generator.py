class ItemCodeGeneratorService:
    @staticmethod
    def generate_fg_code(material_type: str, thickness: str, pattern_code: str) -> str:
        # Normalize pieces
        mat = material_type.strip().upper()
        th = thickness.strip()
        pc = str(pattern_code).strip()
        return f"{mat}-{th}-{pc}"
