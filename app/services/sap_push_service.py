from .sap_client import SapServiceLayerClient
from .warehouse_mapping import WarehouseMappingService

class SapPushService:
    def __init__(self, config):
        self.client = SapServiceLayerClient(config.get('SAP_BASE_URL'), config.get('SAP_USER'), config.get('SAP_PASSWORD'), retries=int(config.get('SAP_RETRY',3)))

    def push_item(self, item_code, item_name, warehouse='FBD-FG'):
        payload = {
            "ItemCode": item_code,
            "ItemName": item_name,
            "InventoryItem": "tYES",
            "SalesItem": "tYES",
            "PurchaseItem": "tNO",
            "ItemWarehouseInfoCollection":[{"WarehouseCode": warehouse}]
        }
        return self.client.post('/b1s/v1/Items', payload)

    def push_bom(self, tree_code, child_items):
        # child_items: list of dicts {ItemCode, Quantity, Warehouse}
        payload = {
            "TreeCode": tree_code,
            "TreeType": "iProductionTree",
            "Quantity": 1,
            "Warehouse": WarehouseMappingService.for_process('FG'),
            "ProductTreeLines": child_items
        }
        return self.client.post('/b1s/v1/ProductTrees', payload)

    def push_full_bom(self, payload):
        # payload contains fg_code, process_items (list), bom_chain (list of parent/child/process)
        responses = []
        # create FG
        fg_code = payload['fg_code']
        fg_name = payload.get('fg_name', f"{payload.get('material_type')} {payload.get('thickness')} FG")
        responses.append({'type':'item','item':fg_code,'response': self.push_item(fg_code, fg_name)})
        # create process items
        for pi in payload.get('process_items', []):
            proc = pi.split('-')[-1]
            warehouse = WarehouseMappingService.for_process(proc)
            responses.append({'type':'item','item':pi,'response': self.push_item(pi, f"{pi} {proc}", warehouse)})
        # push BOMs according to chain
        for link in payload.get('bom_chain', []):
            parent = link['parent']
            child = link['child']
            warehouse = WarehouseMappingService.for_process(link.get('process') or '')
            child_items = [{"ItemCode": child, "Quantity": 1, "Warehouse": warehouse}]
            responses.append({'type':'bom','parent':parent,'child':child,'response': self.push_bom(parent, child_items)})
        return {'status':'completed','responses':responses}
