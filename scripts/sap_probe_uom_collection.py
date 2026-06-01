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
    # items with KGS in any uom field
    for path in [
        "/b1s/v1/Items?$filter=SalUnitMsr eq 'KGS'&$top=1",
        "/b1s/v1/Items?$filter=InvntryUom eq 'KGS'&$top=1",
        "/b1s/v1/UnitOfMeasurements?$top=30",
    ]:
        print('PATH', path.split('?')[0])
        try:
            d = c.get(path)
            print(json.dumps(d, indent=2)[:2500])
        except Exception as e:
            print('ERR', e)
        print()
