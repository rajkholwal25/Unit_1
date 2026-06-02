from ..utils.thickness import thickness_for_item_code


class ItemCodeGeneratorService:
    @staticmethod
    def generate_fg_code(material_type: str, thickness, pattern_code: str, coating: str) -> str:
        mat = material_type.strip().upper()
        th = thickness_for_item_code(thickness)
        if not th:
            raise ValueError('thickness must be a positive number')
        pc = str(pattern_code).strip()
        coat = coating.strip().upper()
        return f"{mat}-{th}-{pc}-{coat}"
