from ..utils.thickness import thickness_for_item_code
from .unit1_item_naming import pattern_segment_for_display


class ItemCodeGeneratorService:
    @staticmethod
    def generate_fg_code(material_type: str, thickness, pattern_code: str, coating: str) -> str:
        """SAP ItemCode / item number — uses pattern **code** (e.g. 1009)."""
        mat = material_type.strip().upper()
        th = thickness_for_item_code(thickness)
        if not th:
            raise ValueError('thickness must be a positive number')
        pc = str(pattern_code).strip()
        coat = coating.strip().upper()
        return f"{mat}-{th}-{pc}-{coat}"

    @staticmethod
    def generate_fg_display_name(material_type: str, thickness, pattern_name: str, coating: str) -> str:
        """SAP ItemName / FG name — uses pattern **name** (e.g. Rectangle)."""
        mat = material_type.strip().upper()
        th = thickness_for_item_code(thickness)
        if not th:
            raise ValueError('thickness must be a positive number')
        pn = pattern_segment_for_display(pattern_name)
        coat = coating.strip().upper()
        return f"{mat}-{th}-{pn}-{coat}"
