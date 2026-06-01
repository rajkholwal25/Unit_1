from app import create_app
from app.services.sap_client import SapServiceLayerClient
import json

code = "BOP-12-1005-CF"
app = create_app()
with app.app_context():
    c = SapServiceLayerClient(
        app.config['SAP_BASE_URL'],
        app.config['SAP_USER'],
        app.config['SAP_PASSWORD'],
        company_db=app.config['SAP_COMPANY_DB'],
        verify_ssl=app.config['SAP_SSL_VERIFY'],
    )
    d = c.get(c.item_path(code))
    if not d:
        print('not found')
    else:
        keys = sorted(d.keys())
        for k in keys:
            if any(x in k.lower() for x in ('uom', 'unit', 'msr', 'material', 'pack', 'sales', 'buy', 'pur', 'inv', 'default')):
                print(k, '=', d.get(k))
        print('collection:', json.dumps(d.get('ItemUnitOfMeasurementCollection'), indent=2))
