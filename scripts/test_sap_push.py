"""One-off SAP Item Master push test (login + push saved FG)."""
import json
import sys

from app import create_app
from app.models import GeneratedFGItem, GeneratedProcessItem
from app.services.sap_push_service import SapPushService

FG_ID = 17  # NYL-12-1001-ALO full template


def main():
    app = create_app()
    with app.app_context():
        cfg = app.config
        if not cfg.get('SAP_BASE_URL'):
            print('ERROR: SAP not configured in .env')
            sys.exit(1)

        fg = GeneratedFGItem.query.get(FG_ID)
        if not fg:
            print(f'ERROR: FG id {FG_ID} not found')
            sys.exit(1)

        procs = [
            p.item_code
            for p in GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).all()
        ]
        payload = {
            'fg_code': fg.item_code,
            'process_items': procs,
            'material_type': fg.material_type,
            'thickness': fg.thickness,
            'coating': fg.coating,
            'template_id': fg.bom_template_id,
        }

        print('=== SAP config ===')
        print('URL:', cfg.get('SAP_BASE_URL'))
        print('CompanyDB:', cfg.get('SAP_COMPANY_DB'))
        print('User:', cfg.get('SAP_USER'))
        print('UOM code:', cfg.get('SAP_UOM_CODE'))
        print('UOM group entry:', cfg.get('SAP_UOM_GROUP_ENTRY'))
        print('UOM KGS entry:', cfg.get('SAP_UOM_KGS_ENTRY'))
        print()
        print('=== Push payload ===')
        print('FG:', payload['fg_code'])
        print('Components:', procs)
        print()

        try:
            svc = SapPushService(cfg)
            preview = SapPushService.preview_item_payloads(payload, cfg)
            print('=== Sample item JSON (FG) ===')
            fg_item = next(i for i in preview['items'] if i['role'] == 'Finished Good')
            print(json.dumps(fg_item['payload'], indent=2))
            print()
            print('=== Pushing to SAP... ===')
            result = svc.push_item_master(payload)
            print(json.dumps(result, indent=2, default=str))
        except Exception as e:
            print('FAILED:', type(e).__name__, str(e))
            sys.exit(1)


if __name__ == '__main__':
    main()
