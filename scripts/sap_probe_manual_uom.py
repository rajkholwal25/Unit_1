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
    d = c.get('/b1s/v1/Items?$top=3')
    for item in d.get('value', []):
        keys = sorted(item.keys())
        interesting = [
            k for k in keys
            if any(
                x in k.lower()
                for x in (
                    'uom', 'unit', 'msr', 'material', 'pack', 'sales', 'buy',
                    'pur', 'inv', 'gst', 'tax', 'currency', 'role', 'roll',
                )
            )
            or k.startswith('U_')
        ]
        print('---', item.get('ItemCode'), '---')
        for k in interesting:
            print(f'  {k}: {item.get(k)}')
        print()
