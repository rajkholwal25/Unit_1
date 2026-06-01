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
    keys = [
        'MaterialType', 'UoMGroupEntry', 'InventoryUoMEntry', 'InvntryUom', 'SalUnitMsr',
        'BuyUnitMsr', 'SalesUnit', 'PurchaseUnit', 'SalesPackagingUnit', 'PurchasePackagingUnit',
        'InventoryUOM', 'BaseUnitName', 'PricingUnit',
    ]
    for mt in ['mt_RawMaterial', 'mt_FinishedGoods']:
        path = f"/b1s/v1/Items?$filter=MaterialType eq '{mt}'&$top=1"
        d = c.get(path)
        if d and d.get('value'):
            it = d['value'][0]
            print('===', mt, it['ItemCode'], '===')
            for k in keys:
                print(k, '=', it.get(k))
            print()

    # search packaging Role
    d2 = c.get("/b1s/v1/UnitOfMeasurements?$filter=contains(Code,'Role') or contains(Name,'Role')&$top=10")
    print('=== UoM Role search ===')
    for u in (d2 or {}).get('value', []):
        print(u.get('AbsEntry'), u.get('Code'), u.get('Name'))
