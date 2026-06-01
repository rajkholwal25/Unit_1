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
    code = 'TEST-M-V3'
    d = c.get(f"/b1s/v1/Items('{code}')")
    for k in sorted(d.keys()):
        if 'uom' in k.lower() or 'pack' in k.lower() or 'default' in k.lower() or 'material' in k.lower():
            print(k, '=', d.get(k))
    print('collection:', json.dumps(d.get('ItemUnitOfMeasurementCollection'), indent=2))
