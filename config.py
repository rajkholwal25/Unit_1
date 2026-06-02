import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / '.env')

def _sap_service_root():
    """Host root for Service Layer (paths append /b1s/v1/...)."""
    url = (
        os.getenv('SAP_SERVICE_LAYER_URL')
        or os.getenv('SAP_BASE_URL')
        or ''
    ).strip().rstrip('/')
    if url.endswith('/b1s/v1'):
        url = url[: -len('/b1s/v1')]
    return url.rstrip('/')


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL') or 'sqlite:///bom.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SAP_SERVICE_LAYER_URL = os.getenv('SAP_SERVICE_LAYER_URL')
    SAP_BASE_URL = _sap_service_root()
    SAP_COMPANY_DB = os.getenv('SAP_COMPANY_DB')
    SAP_USER = os.getenv('SAP_USERNAME') or os.getenv('SAP_USER')
    SAP_PASSWORD = os.getenv('SAP_PASSWORD')
    SAP_OVERRIDE_POSTING_DATE = os.getenv('SAP_OVERRIDE_POSTING_DATE')
    SAP_SSL_VERIFY = os.getenv('SAP_SSL_VERIFY', 'true').lower() in ('1', 'true', 'yes')
    SAP_RETRY = int(os.getenv('SAP_RETRY', '3'))
    # Item Master defaults (SAP B1)
    SAP_FG_ITEMS_GROUP = int(os.getenv('SAP_FG_ITEMS_GROUP', '100'))
    SAP_COMPONENT_ITEMS_GROUP = int(os.getenv('SAP_COMPONENT_ITEMS_GROUP', '107'))
    # UoM manual (-1): KGS for inventory/sales/purchase; packaging UoM code (e.g. Role)
    SAP_UOM_CODE = (os.getenv('SAP_UOM_CODE') or 'KGS').strip().upper()
    _uom_group = (os.getenv('SAP_UOM_GROUP_ENTRY') or '-1').strip()
    SAP_UOM_GROUP_ENTRY = int(_uom_group)
    _uom_kgs = (os.getenv('SAP_UOM_KGS_ENTRY') or '1').strip()
    SAP_UOM_KGS_ENTRY = int(_uom_kgs)
    SAP_PACK_UOM_CODE = (os.getenv('SAP_PACK_UOM_CODE') or 'Role').strip()
    # SAP desktop UI on this tenant shows the opposite enum label (FG needs mt_RawMaterial in API).
    _invert_mt = os.getenv('SAP_MATERIAL_TYPE_INVERT_UI', 'true').lower() in ('1', 'true', 'yes')
    if _invert_mt:
        _default_fg_mt = 'mt_RawMaterial'
        _default_comp_mt = 'mt_FinishedGoods'
    else:
        _default_fg_mt = 'mt_FinishedGoods'
        _default_comp_mt = 'mt_RawMaterial'
    SAP_MATERIAL_TYPE_INVERT_UI = _invert_mt
    SAP_MATERIAL_TYPE_FG = os.getenv('SAP_MATERIAL_TYPE_FG', _default_fg_mt)
    SAP_MATERIAL_TYPE_COMPONENT = os.getenv('SAP_MATERIAL_TYPE_COMPONENT', _default_comp_mt)
    SAP_PRICING_UNIT = int(os.getenv('SAP_PRICING_UNIT', '-1'))
    SAP_HSN_CODE = os.getenv('SAP_HSN_CODE', '3921.90.94')
    SAP_CHAPTER_ID = os.getenv('SAP_CHAPTER_ID', '')
    SAP_ITEM_TAX_RATE = int(os.getenv('SAP_ITEM_TAX_RATE', '18'))
    SAP_GST_TAX_CATEGORY = os.getenv('SAP_GST_TAX_CATEGORY', 'gtc_Regular')
    BOM_YIELD_LOSS_PCT = float(os.getenv('BOM_YIELD_LOSS_PCT', '2'))

class ProdConfig(Config):
    pass

class DevConfig(Config):
    DEBUG = True

config = {
    'development': DevConfig,
    'production': ProdConfig,
}
