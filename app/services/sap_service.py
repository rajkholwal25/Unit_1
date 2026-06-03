import requests
import urllib3
from flask import current_app

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SAPServiceLayer:
    """Client for SAP Business One Service Layer REST API."""

    def __init__(self):
        self.base_url = current_app.config.get('SAP_SERVICE_LAYER_URL', '').rstrip('/')
        self.company_db = current_app.config.get('SAP_COMPANY_DB', '')
        self.username = current_app.config.get('SAP_USERNAME', '')
        self.password = current_app.config.get('SAP_PASSWORD', '')
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        self._logged_in = False

    def login(self):
        """Authenticate with SAP B1 Service Layer. Returns True on success."""
        if not self.base_url:
            raise ValueError('SAP Service Layer URL is not configured.')

        payload = {
            'CompanyDB': self.company_db,
            'UserName': self.username,
            'Password': self.password,
        }

        resp = self.session.post(f'{self.base_url}/Login', json=payload, timeout=30)

        if resp.status_code == 200:
            self._logged_in = True
            return True

        return False

    def logout(self):
        """End the SAP session."""
        if self._logged_in:
            try:
                self.session.post(f'{self.base_url}/Logout', timeout=10)
            except Exception:
                pass
            self._logged_in = False

    def test_connection(self):
        """Test if the Service Layer is reachable and credentials are valid."""
        return self.login()

    def _get(self, endpoint, params=None):
        """Generic GET request."""
        resp = self.session.get(f'{self.base_url}/{endpoint}', params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, endpoint, data):
        """Generic POST request."""
        resp = self.session.post(f'{self.base_url}/{endpoint}', json=data, timeout=30)
        return resp

    def get_business_partners(self, card_type='C'):
        """
        Fetch Business Partners (customers by default).
        card_type: 'C' = Customer, 'S' = Supplier, 'L' = Lead
        """
        partners = []
        skip = 0
        top = 50

        while True:
            params = {
                '$filter': f"CardType eq '{card_type}'",
                '$select': 'CardCode,CardName,Phone1,EmailAddress,ContactPerson',
                '$top': top,
                '$skip': skip,
            }
            data = self._get('BusinessPartners', params=params)
            batch = data.get('value', [])
            if not batch:
                break
            partners.extend(batch)
            skip += top

        return partners

    def get_items(self):
        """Fetch Item Master Data."""
        items = []
        skip = 0
        top = 50

        while True:
            params = {
                '$select': 'ItemCode,ItemName,QuantityOnStock',
                '$top': top,
                '$skip': skip,
            }
            data = self._get('Items', params=params)
            batch = data.get('value', [])
            if not batch:
                break
            items.extend(batch)
            # Service Layer may cap rows per page (~20) regardless of $top.
            # Always advance by actual batch size to avoid missing rows.
            skip += len(batch)

        return items

    def create_production_order(self, job_card):
        """
        Create a Production Order in SAP from a Job Card.
        Returns dict with 'success', 'DocEntry', or 'error'.
        """
        lines = []
        for mat in job_card.materials.all():
            line = {
                'ItemNo': mat.material_code or '',
                'BaseQuantity': mat.quantity_required or 0,
            }
            lines.append(line)

        payload = {
            'ItemNo': job_card.item_code or '',
            'ProductionOrderType': 'bopt_Standard',
            'PlannedQuantity': job_card.quantity,
            'DueDate': job_card.delivery_date.isoformat() if job_card.delivery_date else None,
            'Remarks': f'Job Card: {job_card.job_card_number}',
            'ProductionOrderLines': lines,
        }

        resp = self._post('ProductionOrders', payload)

        if resp.status_code in (200, 201):
            result = resp.json()
            return {
                'success': True,
                'DocEntry': result.get('DocEntry'),
                'DocNum': result.get('DocNum'),
            }
        else:
            error_detail = ''
            try:
                err = resp.json()
                error_detail = err.get('error', {}).get('message', {}).get('value', str(resp.text))
            except Exception:
                error_detail = resp.text
            return {'success': False, 'error': error_detail}

    def create_bom(self, job_card):
        """
        Create a Bill of Materials in SAP from job card materials.
        Returns dict with 'success' or 'error'.
        """
        bom_lines = []
        for mat in job_card.materials.all():
            bom_lines.append({
                'ItemNo': mat.material_code or '',
                'Quantity': mat.quantity_required or 0,
            })

        payload = {
            'TreeType': 'iProductionTree',
            'Quantity': job_card.quantity,
            'ProductionOrderLines': bom_lines,
        }

        if job_card.item_code:
            payload['TreeCode'] = job_card.item_code

        resp = self._post('BillOfMaterials', payload)

        if resp.status_code in (200, 201):
            result = resp.json()
            return {'success': True, 'TreeCode': result.get('TreeCode')}
        else:
            error_detail = ''
            try:
                err = resp.json()
                error_detail = err.get('error', {}).get('message', {}).get('value', str(resp.text))
            except Exception:
                error_detail = resp.text
            return {'success': False, 'error': error_detail}
