import os
from urllib.parse import quote_plus

from sqlalchemy.engine import URL
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _normalize_sap_udt_table_code(raw: str) -> str:
    """SAP client shows @MJD1; Service Layer uses table code MJD1 → /U_MJD1. Strip @ and U_ if pasted."""
    s = (raw or 'MJD1').strip()
    if s.startswith('@'):
        s = s[1:]
    if s.upper().startswith('U_'):
        s = s[2:]
    return s or 'MJD1'


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = False

    DB_TYPE = os.getenv('DB_TYPE', 'sqlite')

    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = os.getenv('DB_PORT', '3306')
    DB_NAME = os.getenv('DB_NAME', 'jobcard_db')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')

    if DB_TYPE == 'mysql':
        SQLALCHEMY_DATABASE_URI = URL.create(
            drivername="mysql+pymysql",
            username=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=int(DB_PORT) if str(DB_PORT).isdigit() else None,
            database=DB_NAME,
            query={"charset": "utf8mb4"},
        )
    else:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'jobcard.db')}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    if DB_TYPE == 'mysql':
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_recycle': 280,
            'pool_pre_ping': True,
            'pool_size': 5,
            'max_overflow': 10,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {}

    ITEMS_PER_PAGE = int(os.getenv('ITEMS_PER_PAGE', '20'))
    JOB_NO_PREFIX = os.getenv('JOB_NO_PREFIX', 'JC')
    JOB_PDF_UPLOAD_DIR = os.getenv(
        'JOB_PDF_UPLOAD_DIR',
        r'\\192.168.3.12\JDF',
    )
    # Optional: if JOB_PDF_UPLOAD_DIR points to a Windows UNC share (\\server\share),
    # provide credentials so the app can establish access before writing ZIPs.
    JOB_PDF_SHARE_USERNAME = os.getenv('JOB_PDF_SHARE_USERNAME', '')
    JOB_PDF_SHARE_PASSWORD = os.getenv('JOB_PDF_SHARE_PASSWORD', '')
    PRINECT_PTK_FROM_IDENTITY = os.getenv('PRINECT_PTK_FROM_IDENTITY', 'FromIdentity')
    PRINECT_PTK_TO_IDENTITY = os.getenv('PRINECT_PTK_TO_IDENTITY', 'ToIdentity')
    PRINECT_PTK_CATALOG_ID = os.getenv('PRINECT_PTK_CATALOG_ID', 'SM74_4')
    PRINECT_PTK_MEDIA_QUALITY = os.getenv('PRINECT_PTK_MEDIA_QUALITY', 'UNKNOWN')
    SAP_VERIFY_SSL = os.getenv('SAP_VERIFY_SSL', 'False').lower() != 'false'
    # Reuse one Service Layer HTTP session (cookies) across Flask requests — avoids Login on every API call.
    SAP_REUSE_HTTP_SESSION = os.getenv(
        'SAP_REUSE_HTTP_SESSION', 'true'
    ).lower() in ('1', 'true', 'yes')
    try:
        SAP_REQUEST_TIMEOUT = int(os.getenv('SAP_REQUEST_TIMEOUT', '120'))
    except ValueError:
        SAP_REQUEST_TIMEOUT = 120

    SAP_SERVICE_LAYER_URL = os.getenv('SAP_SERVICE_LAYER_URL', '')
    # SAP mirror tables (sap_customer_mirror): refresh from Service Layer
    SAP_MIRROR_AUTO_SYNC_ENABLED = os.getenv('SAP_MIRROR_AUTO_SYNC_ENABLED', 'true').lower() in (
        '1', 'true', 'yes',
    )
    try:
        SAP_MIRROR_SYNC_INTERVAL_HOURS = int(os.getenv('SAP_MIRROR_SYNC_INTERVAL_HOURS', '24'))
    except ValueError:
        SAP_MIRROR_SYNC_INTERVAL_HOURS = 24
    SAP_MIRROR_SYNC_ON_STARTUP = os.getenv('SAP_MIRROR_SYNC_ON_STARTUP', 'false').lower() in (
        '1', 'true', 'yes',
    )
    SAP_COMPANY_DB = os.getenv('SAP_COMPANY_DB', '')
    SAP_USERNAME = os.getenv('SAP_USERNAME', '')
    SAP_PASSWORD = os.getenv('SAP_PASSWORD', '')
    # If set (e.g. '2025-01-01'), this date is forced for all SAP Production Order postings.
    # Leave empty or unset to use the actual today's date.
    SAP_OVERRIDE_POSTING_DATE = os.getenv('SAP_OVERRIDE_POSTING_DATE', '').strip() or None

    # MJD1 data source (matches DPR Generator: UDO OMJD + line rows, not standalone U_MJD1)
    # udo: list GET /OMJD (no $expand), then GET /OMJD(DocEntry) per job — avoids invalid OData $expand names.
    # udt = GET /U_<table> for a plain user-defined table.
    SAP_MJD1_SOURCE = os.getenv('SAP_MJD1_SOURCE', 'udo').strip().lower()
    SAP_MJD1_UDO_OBJECT = os.getenv('SAP_MJD1_UDO_OBJECT', 'OMJD').strip()
    # JSON property name for line rows on OMJD response (DPR uses MJD1Collection; your Service Layer may differ)
    SAP_MJD1_UDO_LINES_JSON_KEY = os.getenv('SAP_MJD1_UDO_LINES_JSON_KEY', '').strip()
    # Legacy alias: if SAP_MJD1_UDO_LINES_JSON_KEY is empty, this is the first key we try (default MJD1Collection)
    SAP_MJD1_UDO_LINES_COLLECTION = os.getenv('SAP_MJD1_UDO_LINES_COLLECTION', 'MJD1Collection').strip()
    # OData NavigationProperty name for $expand only if a plain GET does not return lines (see $metadata EntityType OMJD)
    SAP_MJD1_UDO_EXPAND_NAV = os.getenv('SAP_MJD1_UDO_EXPAND_NAV', '').strip()
    # Composite row key when a line has no Code: "{DocEntry}|{LineNum}"
    SAP_MJD1_LINE_KEY_SEPARATOR = os.getenv('SAP_MJD1_LINE_KEY_SEPARATOR', '|').strip() or '|'

    # SAP UDT shown as @MJD1 in the client: table code is MJD1 (no @). Service Layer exposes it as U_MJD1.
    # Only when SAP_MJD1_SOURCE=udt. If GET /U_<name> fails, set SAP_MJD1_ODATA_PATH from $metadata.
    SAP_MJD1_ODATA_PATH = os.getenv('SAP_MJD1_ODATA_PATH', '').strip()
    SAP_UDT_MJD1_TABLE = _normalize_sap_udt_table_code(os.getenv('SAP_UDT_MJD1_TABLE', 'MJD1'))
    SAP_MJD1_FIELD_CODE = os.getenv('SAP_MJD1_FIELD_CODE', 'Code')
    SAP_MJD1_FIELD_CUSTOMER_NAME = os.getenv('SAP_MJD1_FIELD_CUSTOMER_NAME', 'U_CustName')
    # Comma-separated extra UDF/header fields to try if primary is empty (OMJD often has CardName on header)
    SAP_MJD1_CUSTOMER_NAME_FALLBACKS = os.getenv('SAP_MJD1_CUSTOMER_NAME_FALLBACKS', '').strip()
    SAP_MJD1_FIELD_SO = os.getenv('SAP_MJD1_FIELD_SO', 'U_SoNo')
    SAP_MJD1_FIELD_FG = os.getenv('SAP_MJD1_FIELD_FG', 'U_FGCode')
    SAP_MJD1_FIELD_FG_NAME = os.getenv('SAP_MJD1_FIELD_FG_NAME', 'U_FGName')
    SAP_MJD1_FIELD_QTY = os.getenv('SAP_MJD1_FIELD_QTY', 'U_DispatchQty')
    # Optional: SAP Business Partner CardCode on the UDT row (else customer name is used as code)
    SAP_MJD1_FIELD_CARD_CODE = os.getenv('SAP_MJD1_FIELD_CARD_CODE', '')

    # SAP job-card prefill lookup (OMJD + child rows). These are field-name
    # candidates; the app tries them in order until a populated value is found.
    SAP_JOB_CARD_DOCNUM_FIELDS = os.getenv(
        'SAP_JOB_CARD_DOCNUM_FIELDS',
        'DocNum,DocEntry,Code',
    ).strip()
    SAP_JOB_CARD_SERIES_FIELDS = os.getenv(
        'SAP_JOB_CARD_SERIES_FIELDS',
        'Series,SeriesCode,U_Series',
    ).strip()
    SAP_JOB_CARD_HEADER_CUSTOMER_CODE_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_CUSTOMER_CODE_FIELDS',
        'CardCode,U_CardCode,U_CustCode',
    ).strip()
    SAP_JOB_CARD_HEADER_CUSTOMER_NAME_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_CUSTOMER_NAME_FIELDS',
        'CardName,U_CustName,U_Customer,U_PrNa',
    ).strip()
    SAP_JOB_CARD_HEADER_TITLE_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_TITLE_FIELDS',
        'Name,U_Title,U_JobName,Remarks,Comments',
    ).strip()
    SAP_JOB_CARD_HEADER_SO_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_SO_FIELDS',
        'U_SoNo,SoNo,SalesOrder',
    ).strip()
    SAP_JOB_CARD_HEADER_FG_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_FG_FIELDS',
        'U_FGCo,U_FGCode,U_FG,ItemCode,ItemNo',
    ).strip()
    SAP_JOB_CARD_HEADER_FG_NAME_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_FG_NAME_FIELDS',
        'U_FGNa,U_FGName,U_FGDesc,ItemName,ItemDescription,Dscription',
    ).strip()
    SAP_JOB_CARD_HEADER_QTY_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_QTY_FIELDS',
        'U_Dqty,U_Prqty,U_DispatchQty,Quantity,PlannedQty',
    ).strip()
    SAP_JOB_CARD_HEADER_UPS_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_UPS_FIELDS',
        'U_NoUps,U_UPS,UPS',
    ).strip()
    SAP_JOB_CARD_HEADER_LENGTH_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_LENGTH_FIELDS',
        'U_Len,U_Length,U_CartonLength,Length',
    ).strip()
    SAP_JOB_CARD_HEADER_WIDTH_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_WIDTH_FIELDS',
        'U_Wid,U_Width,U_CartonWidth,Width',
    ).strip()
    SAP_JOB_CARD_HEADER_HEIGHT_FIELDS = os.getenv(
        'SAP_JOB_CARD_HEADER_HEIGHT_FIELDS',
        'U_Hei,U_Height,U_CartonHeight,Height',
    ).strip()
    SAP_JOB_CARD_SELECTED_LINE_KEYS = os.getenv(
        'SAP_JOB_CARD_SELECTED_LINE_KEYS',
        'MJD1Collection,MJD1,U_MJD1,MJD1Rows,MJD1LineCollection',
    ).strip()
    SAP_JOB_CARD_DETAIL_LINE_KEYS = os.getenv(
        'SAP_JOB_CARD_DETAIL_LINE_KEYS',
        'MJD2Collection,MJD2,U_MJD2,MJD2Rows,MJD2LineCollection',
    ).strip()
    SAP_JOB_CARD_DETAIL_SEQ_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_SEQ_FIELDS',
        'LineNum,SeqNo,Sequence,StepNo',
    ).strip()
    SAP_JOB_CARD_DETAIL_PROCESS_CODE_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_PROCESS_CODE_FIELDS',
        'U_JoEl,U_PQC,U_PriSt,ProcessCode,U_ProcessCode,OperationCode,Code',
    ).strip()
    SAP_JOB_CARD_DETAIL_STEP_NAME_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_STEP_NAME_FIELDS',
        'U_JoEl,U_PQC,U_PriSt,StepName,ProcessName,U_ProcessName,Name,Dscription,ItemName',
    ).strip()
    SAP_JOB_CARD_DETAIL_UPS_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_UPS_FIELDS',
        'U_NoUps,U_UPS,UPS,Ups',
    ).strip()
    SAP_JOB_CARD_DETAIL_OUTPUT_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_OUTPUT_FIELDS',
        'U_RaItC,U_RaItN,OutputItemCode,U_OutputItemCode,ItemCode,ItemNo',
    ).strip()
    SAP_JOB_CARD_DETAIL_QTY_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_QTY_FIELDS',
        'U_PrSht,U_Oqty,U_TotW,PlannedQuantity,BaseQuantity,Quantity,U_Qty',
    ).strip()
    SAP_JOB_CARD_DETAIL_PAPER_QUALITY_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_PAPER_QUALITY_FIELDS',
        'U_Grade,U_GRADE,U_PQC',
    ).strip()
    SAP_JOB_CARD_DETAIL_GSM_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_GSM_FIELDS',
        'U_GSM,U_Gsm,U_gsm',
    ).strip()
    SAP_JOB_CARD_DETAIL_MILL_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_MILL_FIELDS',
        'U_PBr,U_Mill,U_MILL',
    ).strip()
    SAP_JOB_CARD_DETAIL_FRONT_COLOUR_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_FRONT_COLOUR_FIELDS',
        'U_Front,U_FRONT',
    ).strip()
    SAP_JOB_CARD_DETAIL_BACK_COLOUR_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_BACK_COLOUR_FIELDS',
        'U_Back,U_BACK',
    ).strip()
    SAP_JOB_CARD_DETAIL_PRINT_STYLE_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_PRINT_STYLE_FIELDS',
        'U_PriSt,U_PrSi',
    ).strip()
    SAP_JOB_CARD_DETAIL_PRINT_TYPE_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_PRINT_TYPE_FIELDS',
        'U_Pltty,U_PType',
    ).strip()
    SAP_JOB_CARD_DETAIL_PRINT_TYPE_FLAG_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_PRINT_TYPE_FLAG_FIELDS',
        'U_Con,U_met',
    ).strip()
    SAP_JOB_CARD_DETAIL_DIE_NO_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_DIE_NO_FIELDS',
        'U_Dia,U_Die,U_DieNo,U_Die_No',
    ).strip()
    SAP_JOB_CARD_DETAIL_WAREHOUSE_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_WAREHOUSE_FIELDS',
        'Warehouse,WhsCode',
    ).strip()
    SAP_JOB_CARD_DETAIL_UOM_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_UOM_FIELDS',
        'UoM,UOM,MeasureUnit',
    ).strip()
    SAP_JOB_CARD_DETAIL_REMARK_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_REMARK_FIELDS',
        'U_SpeIn,Remarks,U_Remarks,Comments,ProductionOrderRemarks',
    ).strip()
    SAP_JOB_CARD_DETAIL_INPUT_KEYS = os.getenv(
        'SAP_JOB_CARD_DETAIL_INPUT_KEYS',
        'Inputs,InputLines,Materials,Components,ChildRows',
    ).strip()
    SAP_JOB_CARD_DETAIL_INPUT_ITEM_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_INPUT_ITEM_FIELDS',
        'ItemCode,U_ItemCode,MaterialCode',
    ).strip()
    SAP_JOB_CARD_DETAIL_INPUT_NAME_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_INPUT_NAME_FIELDS',
        'ItemName,Description,Dscription',
    ).strip()
    SAP_JOB_CARD_DETAIL_INPUT_QTY_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_INPUT_QTY_FIELDS',
        'Quantity,Qty,PlannedQuantity,BaseQuantity',
    ).strip()
    SAP_JOB_CARD_DETAIL_INPUT_WAREHOUSE_FIELDS = os.getenv(
        'SAP_JOB_CARD_DETAIL_INPUT_WAREHOUSE_FIELDS',
        'Warehouse,WhsCode',
    ).strip()
    SAP_JOB_CARD_OWOR_LINK_FIELDS = os.getenv(
        'SAP_JOB_CARD_OWOR_LINK_FIELDS',
        'U_OWORDocEntry,U_ProdOrderEntry,U_ProdOrderDocEntry,OWORDocEntry',
    ).strip()

    # Customer dropdown for /api/sap/mjd1/customers:
    # mjd1 = distinct names from MJD1 rows only (OMJD mode may be a small set).
    # business_partners = CardName from SAP Business Partners (paginated).
    # merged = union of mjd1 distinct + BP names (recommended when U_MJD1 OData is unavailable).
    # ocrd_series = OCRD via Service Layer /BusinessPartners: CardCode + CardName, filter Series (default 1).
    SAP_MJD1_CUSTOMER_LIST_SOURCE = os.getenv(
        'SAP_MJD1_CUSTOMER_LIST_SOURCE', 'mjd1'
    ).strip().lower()
    # Used when SAP_MJD1_CUSTOMER_LIST_SOURCE=ocrd_series (SAP OCRD "Series" field).
    try:
        SAP_OCRD_SERIES = int(os.getenv('SAP_OCRD_SERIES', '1'))
    except ValueError:
        SAP_OCRD_SERIES = 1

    # Rotating file logs under project ``logs/`` (see app/logging_config.py)
    APP_LOG_DIR = os.getenv('APP_LOG_DIR', os.path.join(BASE_DIR, 'logs'))
    APP_LOG_FILE = os.getenv('APP_LOG_FILE', 'app.log')
    APP_LOG_LEVEL = os.getenv('APP_LOG_LEVEL', 'INFO')
    APP_LOG_MAX_BYTES = int(os.getenv('APP_LOG_MAX_BYTES', str(2 * 1024 * 1024)))
    APP_LOG_BACKUP_COUNT = int(os.getenv('APP_LOG_BACKUP_COUNT', '5'))
    # Set true to mirror logs to stderr when not in Flask debug mode
    APP_LOG_CONSOLE = os.getenv('APP_LOG_CONSOLE', '').lower() in ('1', 'true', 'yes')

    # Prinect (optional): generate JDF when job card moves to "released"
    PRINECT_ENABLED = os.getenv('PRINECT_ENABLED', '').lower() in ('1', 'true', 'yes')
    PRINECT_JDF_ENDPOINT = os.getenv('PRINECT_JDF_ENDPOINT', '').strip()

    # ORDR+RDR1 open SO list (job card): filter by DocumentStatus + exclude Cancelled Y/C + line open qty
    SAP_ORDER_FILTER_DOCUMENT_STATUS_OPEN = os.getenv(
        'SAP_ORDER_FILTER_DOCUMENT_STATUS_OPEN', 'true'
    ).lower() in ('1', 'true', 'yes')
    SAP_ORDER_FILTER_CANCELLED_YC = os.getenv(
        'SAP_ORDER_FILTER_CANCELLED_YC', 'true'
    ).lower() in ('1', 'true', 'yes')
    # Comma-separated JSON property names to try on each RDR1 line (OpenCreQty = DB field name in many B1 builds)
    SAP_ORDER_LINE_OPEN_QTY_FIELDS = os.getenv(
        'SAP_ORDER_LINE_OPEN_QTY_FIELDS',
        'OpenCreQty,OpenQuantity,OpenQty,RemainingOpenQuantity',
    ).strip()
    # If no open-qty field is present on a line (common on some /Orders/DocumentLines responses),
    # treat non-closed lines with Quantity > 0 as open so the SO list is not empty.
    SAP_ORDER_LINE_OPEN_FALLBACK_QUANTITY = os.getenv(
        'SAP_ORDER_LINE_OPEN_FALLBACK_QUANTITY', 'true'
    ).lower() in ('1', 'true', 'yes')
    # Service Layer navigation name for sales order lines ($expand). Some builds reject DocumentLines on Document.
    # Comma-separated try-order; empty = auto-try DocumentLines, OrderLines, documentLines, orderLines
    SAP_ORDER_LINES_EXPAND_NAV = os.getenv('SAP_ORDER_LINES_EXPAND_NAV', '').strip()
    # Artwork number field(s) on Sales Order line (RDR1) / Service Layer JSON.
    # Comma-separated; the first non-empty value found is used for printing.
    SAP_ORDER_LINE_ARTWORK_FIELDS = os.getenv(
        'SAP_ORDER_LINE_ARTWORK_FIELDS',
        'U_ArtworkNo,U_ArtWorkNo,U_Artwork,U_ArtNo,U_ARTNO,U_Art_Num',
    ).strip()
    # Item code field(s) on Sales Order line (RDR1) / Service Layer JSON for printing (after artwork column).
    SAP_ORDER_LINE_ITEMCODE_FIELDS = os.getenv(
        'SAP_ORDER_LINE_ITEMCODE_FIELDS',
        'ItemCode,U_ItemCode,U_Item_Code',
    ).strip()
    # Service Layer entity for salesperson master (OSLP); some builds expose SalesPersons.
    SAP_SALESPERSON_ENTITY = os.getenv('SAP_SALESPERSON_ENTITY', 'SalesPersons').strip() or 'SalesPersons'
    # Print slip: resolve SlpCode from ORDR lines containing job FG items; pick highest DocEntry in scan window.
    try:
        SAP_PRINT_SLP_SCAN_LIMIT = int(os.getenv('SAP_PRINT_SLP_SCAN_LIMIT', '500'))
    except ValueError:
        SAP_PRINT_SLP_SCAN_LIMIT = 500
    SAP_PRINT_SLP_OPEN_ONLY = os.getenv('SAP_PRINT_SLP_OPEN_ONLY', 'true').lower() in (
        '1', 'true', 'yes',
    )
    # If true, list SO headers from /Orders (CardCode filter) without per-line open-qty checks.
    # Default true: many Service Layer builds return empty/malformed DocumentLines for filtering.
    # Set false to require at least one "open" line (see SAP_ORDER_LINE_OPEN_*).
    SAP_ORDER_LIST_SKIP_LINE_FILTER = os.getenv(
        'SAP_ORDER_LIST_SKIP_LINE_FILTER', 'true'
    ).lower() in ('1', 'true', 'yes')

    # SAP settings for BOM / Manufacturing process items
    try:
        SAP_BOM_PROCESS_ITEM_GROUP_CODE = int(os.getenv('SAP_BOM_PROCESS_ITEM_GROUP_CODE', '115'))
    except ValueError:
        SAP_BOM_PROCESS_ITEM_GROUP_CODE = 115
    UNIT1_DEFAULT_UOM = (os.getenv('UNIT1_DEFAULT_UOM', 'KGS') or 'KGS').strip().upper()
    SAP_BOM_PROCESS_ITEM_UOM = (
        os.getenv('SAP_BOM_PROCESS_ITEM_UOM', 'KGS').strip() or UNIT1_DEFAULT_UOM
    )

    # Default warehouse used when creating Special Production Orders in SAP.
    # Must belong to the same branch as your SAP user session.
    # Branch 3 (Unit II) warehouses: II-AQ, II-ASS, II-BDG, II-CORU, II-DBS, II-DIE, II-EMB
    SAP_DEFAULT_WAREHOUSE = os.getenv('SAP_DEFAULT_WAREHOUSE', 'II-DIE').strip() or 'II-DIE'

