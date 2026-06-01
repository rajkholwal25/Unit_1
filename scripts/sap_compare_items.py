from app import create_app
from app.services.sap_client import SapServiceLayerClient

app = create_app()
with app.app_context():
    c = SapServiceLayerClient(
        app.config['SAP_BASE_URL'],
        app.config['SAP_USER'],
        app.config['SAP_PASSWORD'],
        company_db=app.config['SAP_COMPANY_DB'],
        verify_ssl=app.config['SAP_SSL_VERIFY'],
    )
    for code in ['BOP-12-1005-CF', 'TEST-MANUAL-FG-02', 'FG0000595-GBD-RED SLEEVE-DIE-SLV']:
        d = c.get(c.item_path(code))
        if not d:
            print(code, 'NOT FOUND')
            continue
        print('===', code, '===')
        print('MaterialType', d.get('MaterialType'))
        print('ItemsGroupCode', d.get('ItemsGroupCode'))
        print('UoMGroupEntry', d.get('UoMGroupEntry'))
        print('DefaultSalesUoMEntry', d.get('DefaultSalesUoMEntry'))
        print('DefaultPurchasingUoMEntry', d.get('DefaultPurchasingUoMEntry'))
        print('InventoryUOM', d.get('InventoryUOM'))
        print('SalUnitMsr', d.get('SalUnitMsr'))
        print('BuyUnitMsr', d.get('BuyUnitMsr'))
        print('InvntryUom', d.get('InvntryUom'))
        print('SalesPackagingUnit', d.get('SalesPackagingUnit'))
        print('PurchasePackagingUnit', d.get('PurchasePackagingUnit'))
        print('PurchaseItem', d.get('PurchaseItem'))
        print('collection len', len(d.get('ItemUnitOfMeasurementCollection') or []))
        print()
