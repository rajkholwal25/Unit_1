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
    paths = [
        '/b1s/v1/UnitOfMeasurementGroups?$top=15',
        "/b1s/v1/UnitOfMeasurements?$filter=Code eq 'KGS'&$top=5",
        '/b1s/v1/Items?$top=1&$select=ItemCode,InvntryUom,SalUnitMsr,UoMGroupEntry,InventoryUoMEntry',
    ]
    for path in paths:
        print('===', path.split('?')[0], '===')
        try:
            d = c.get(path)
            print(json.dumps(d, indent=2)[:2000])
        except Exception as e:
            print('ERR', e)
        print()
