import requests
from requests.adapters import HTTPAdapter, Retry

class SapServiceLayerClient:
    def __init__(self, base_url, username, password, retries=3):
        self.base = base_url.rstrip('/')
        self.user = username
        self.pw = password
        self.s = requests.Session()
        retries_cfg = Retry(total=retries, backoff_factor=1, status_forcelist=[502,503,504])
        self.s.mount('https://', HTTPAdapter(max_retries=retries_cfg))

    def post(self, path, json_payload):
        url = f"{self.base}{path}"
        resp = self.s.post(url, json=json_payload, auth=(self.user, self.pw), timeout=30)
        resp.raise_for_status()
        return resp.json()
