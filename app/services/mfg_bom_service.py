"""bom_service.py — Business logic for BOM creation and management."""
from flask_login import current_user

from app.extensions import db
from app.models.mfg_bom import Bom, BomStep, BomStepInput
from app.models.job import JobHeaderLine, JobDetailLine
from app.models.reference import ProcessMaster


def get_or_create_active_bom(detail_line: JobDetailLine, user_id: int = None) -> Bom:
    """Return the active BOM for a detail line, creating v1 if none exists."""
    bom = detail_line.active_bom
    if bom:
        return bom
    return create_bom(detail_line, user_id=user_id)


def create_bom(
    detail_line: JobDetailLine,
    user_id: int = None,
) -> Bom:
    """Create a new BOM version for a header line.

    Deactivates any existing active BOM first (preserving history).
    Does NOT commit.
    """
    actor = user_id or (current_user.id if current_user.is_authenticated else None)

    # Deactivate current active BOM if exists
    existing = detail_line.active_bom
    if existing:
        existing.deactivate()

    # New version number = max(existing versions) + 1
    max_ver = (
        db.session.query(db.func.max(Bom.version))
        .filter_by(detail_line_id=detail_line.id)
        .scalar()
    ) or 0

    bom = Bom(
        detail_line_id=detail_line.id,
        job_id=detail_line.job.job_no if detail_line.job else None,
        version=max_ver + 1,
        is_active=True,
        created_by=actor,
    )
    db.session.add(bom)

    db.session.flush()
    return bom


def clone_bom(source_bom: Bom, target_detail_line: JobDetailLine, user_id: int = None) -> Bom:
    """Clone an existing BOM onto a different (or the same) detail line.

    Useful for applying templates to a new job component.
    Copies all steps and inputs. Does NOT commit.
    """
    actor = user_id or (current_user.id if current_user.is_authenticated else None)

    new_bom = create_bom(target_detail_line, user_id=actor)

    for step in source_bom.steps.all():
        new_step = BomStep(
            bom_id=new_bom.id,
            seq_no=step.seq_no,
            process_code=step.process_code,
            step_name=step.step_name,
            warehouse=step.warehouse,
            sap_warehouse=step.sap_warehouse,
            uom=step.uom,
            planned_qty=step.planned_qty,
            production_order_remarks=step.production_order_remarks,
        )
        db.session.add(new_step)
        db.session.flush()

        for inp in step.inputs.all():
            new_inp = BomStepInput(
                bom_step_id=new_step.id,
                input_type=inp.input_type,
                sap_item_code=inp.sap_item_code,
                description=inp.description,
                uom=inp.uom,
                qty_per_job=inp.qty_per_job,
                sap_warehouse=inp.sap_warehouse,
            )
            db.session.add(new_inp)

    return new_bom


def add_step(
    bom: Bom,
    process_code: str,
    step_name: str = None,
    warehouse: str = None,
    seq_no: int = None,
    sap_warehouse: str = None,
    uom: str = None,
    planned_qty: float = None,
) -> BomStep:
    """Add a process step to a BOM.

    seq_no auto-assigned if not given.
    warehouse defaults from process_master if not given.
    Does NOT commit.
    """
    if seq_no is None:
        max_seq = (
            db.session.query(db.func.max(BomStep.seq_no))
            .filter_by(bom_id=bom.id)
            .scalar()
        ) or 0
        seq_no = max_seq + 10   # gaps of 10 so steps can be reordered later

    # Default warehouse from process master
    if not warehouse:
        process = ProcessMaster.query.filter_by(process_code=process_code).first()
        if process:
            warehouse = process.default_workcenter

    if not step_name:
        process = ProcessMaster.query.filter_by(process_code=process_code).first()
        step_name = process.name if process else process_code

    step = BomStep(
        bom_id=bom.id,
        seq_no=seq_no,
        process_code=process_code,
        step_name=step_name,
        warehouse=warehouse,
        sap_warehouse=sap_warehouse,
        uom=uom,
        planned_qty=planned_qty,
    )
    db.session.add(step)
    db.session.flush()
    return step


def add_input(
    step: BomStep,
    input_type: str,
    sap_item_code: str,
    description: str,
    uom: str,
    qty_per_job,
    sap_warehouse: str = None,
) -> BomStepInput:
    """Add a material/consumable input to a BOM step. Does NOT commit."""
    inp = BomStepInput(
        bom_step_id=step.id,
        input_type=input_type,
        sap_item_code=sap_item_code,
        description=description,
        uom=uom,
        qty_per_job=qty_per_job,
        sap_warehouse=sap_warehouse,
    )
    db.session.add(inp)
    return inp


def autofill_paper_qty(step: BomStep, detail_line: JobDetailLine) -> None:
    """Update the paper raw_material input on a printing step from the detail line.

    Reads total_sheets_with_wastage from the detail line and sets it as
    qty_per_job on any raw_material input in this step.

    Call this whenever dispatch_qty, total_sheets, or wastage_pct changes.
    Does NOT commit.
    """
    if not detail_line or not detail_line.total_sheets_with_wastage:
        return

    for inp in step.inputs.all():
        if inp.input_type == 'raw_material':
            inp.qty_per_job = detail_line.total_sheets_with_wastage
