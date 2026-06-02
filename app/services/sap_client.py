import requests
from requests.adapters import HTTPAdapter, Retry


class SapServiceLayerClient:
    def __init__(self, base_url, username, password, *, company_db=None, verify_ssl=True, retries=3):
        if not base_url:
            raise ValueError('SAP base URL is required (SAP_SERVICE_LAYER_URL or SAP_BASE_URL)')
        self.base = base_url.rstrip('/')
        self.user = username
        self.pw = password
        self.company_db = company_db
        self.verify_ssl = verify_ssl
        self.s = requests.Session()
        retries_cfg = Retry(total=retries, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.s.mount('https://', HTTPAdapter(max_retries=retries_cfg))
        self.s.mount('http://', HTTPAdapter(max_retries=retries_cfg))
        self._login()

    def _login(self):
        if not self.company_db:
            return
        url = f'{self.base}/b1s/v1/Login'
        payload = {
            'CompanyDB': self.company_db,
            'UserName': self.user,
            'Password': self.pw,
        }
        resp = self.s.post(url, json=payload, verify=self.verify_ssl, timeout=30)
        resp.raise_for_status()

    @staticmethod
    def escape_item_code(item_code):
        return str(item_code).replace("'", "''")

    def item_path(self, item_code):
        return f"/b1s/v1/Items('{self.escape_item_code(item_code)}')"

    def product_tree_path(self, tree_code):
        return f"/b1s/v1/ProductTrees('{self.escape_item_code(tree_code)}')"

    def get(self, path):
        url = f'{self.base}{path}'
        resp = self.s.get(url, verify=self.verify_ssl, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    def post(self, path, json_payload):
        url = f'{self.base}{path}'
        resp = self.s.post(url, json=json_payload, verify=self.verify_ssl, timeout=30)
        if not resp.ok:
            detail = resp.text
            try:
                detail = resp.json()
            except Exception:
                pass
            raise requests.HTTPError(
                f'{resp.status_code} {resp.reason} — {detail}',
                response=resp,
            )
        return resp.json() if resp.content else {}

    def patch(self, path, json_payload):
        url = f'{self.base}{path}'
        resp = self.s.patch(url, json=json_payload, verify=self.verify_ssl, timeout=30)
        if not resp.ok:
            detail = resp.text
            try:
                detail = resp.json()
            except Exception:
                pass
            raise requests.HTTPError(
                f'{resp.status_code} {resp.reason} — {detail}',
                response=resp,
            )
        return resp.json() if resp.content else {}

    def delete(self, path):
        url = f'{self.base}{path}'
        resp = self.s.delete(url, verify=self.verify_ssl, timeout=60)
        if resp.status_code == 404:
            return False
        if not resp.ok:
            detail = resp.text
            try:
                detail = resp.json()
            except Exception:
                pass
            raise requests.HTTPError(
                f'{resp.status_code} {resp.reason} — {detail}',
                response=resp,
            )
        return True
