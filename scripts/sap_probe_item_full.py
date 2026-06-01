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
    item = d['value'][0]
    for k in sorted(item.keys()):
        if 'gst' in k.lower() or 'tax' in k.lower() or k.startswith('U_'):
            print(k, '=', item[k])
