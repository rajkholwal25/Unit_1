from app import create_app
from app.services.sap_client import SapServiceLayerClient
import json

app = create_app()
with app.app_context():
    c = SapServiceLayerClient(
        app.config['SAP_BASE_URL'],
        app.config['SAP_USER'],
        app.config['SAP_PASSWORD'],
        company_db=app.config['SAP_COMPANY_DB'],
        verify_ssl=app.config['SAP_SSL_VERIFY'],
    )
    d = c.get('/b1s/v1/Items?$top=1')
    if d and d.get('value'):
        item = d['value'][0]
        keys = [
            'ItemCode', 'ItemsGroupCode', 'UoMGroupEntry', 'InventoryUoMEntry',
            'SalesUoMEntry', 'PurchaseUoMEntry', 'InvntryUom', 'SalUnitMsr',
            'BuyUnitMsr', 'SalesItem', 'PurchaseItem', 'InventoryItem',
        ]
        print(json.dumps({k: item.get(k) for k in keys if k in item}, indent=2))
        udfs = {k: v for k, v in item.items() if k.startswith('U_')}
        print('UDFs:', json.dumps(udfs, indent=2)[:1500])
    else:
        print('no items', d)
