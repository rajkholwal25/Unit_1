"""Unit 2 CLI + Unit 1 process_master seeds."""
import click
from flask import Flask


def register_mfg_commands(app: Flask) -> None:
    @app.cli.command('setup-database')
    @click.option(
        '--skip-seed',
        is_flag=True,
        help='Only run migrations; do not seed process_master.',
    )
    def setup_database(skip_seed):
        """Create all Unit 1 + Unit 2 tables on the database in DATABASE_URL."""
        from flask_migrate import upgrade

        from .core.bootstrap import ensure_default_manager

        click.echo('Running Alembic migrations (all tables)...')
        upgrade()
        click.echo('Migrations complete.')

        if not skip_seed:
            from .extensions import db
            from .models.reference import ProcessMaster

            from .services.unit1_processes import seed_unit1_process_master

            click.echo(seed_unit1_process_master())

        ensure_default_manager(app)
        click.echo('Default login (if missing): manager@test.com / test@123')
        click.echo('Done. Point DATABASE_URL in .env to this DB and restart the app.')

    @app.cli.command('seed-processes')
    def seed_processes():
        """Unit 1 only: EMB, SLT, MET, COT in process_master."""
        from .services.unit1_processes import seed_unit1_process_master

        click.echo(seed_unit1_process_master())

    @app.cli.command('sync-sap-customers')
    def sync_sap_customers():
        from .services.sap_mirror_sync import sync_customers_from_sap

        click.echo(f'Synced {sync_customers_from_sap()} customers.')

    @app.cli.command('sync-sap-items')
    def sync_sap_items():
        from .services.sap_mirror_sync import sync_items_from_sap

        click.echo(f'Synced {sync_items_from_sap()} items.')

    @app.cli.command('sync-sap-mirror')
    @click.option('--scope', type=click.Choice(['all', 'customers']), default='all')
    def sync_sap_mirror(scope):
        from flask import current_app

        from .services.sap_mirror_sync import run_full_mirror_sync

        out = run_full_mirror_sync(current_app, scope=scope)
        click.echo(f'SAP mirror sync OK: {out}')

    @app.cli.command('cleanup-duplicate-so-jobs')
    @click.option(
        '--execute',
        is_flag=True,
        help='Apply cancellations. Default is dry-run preview only.',
    )
    def cleanup_duplicate_so_jobs(execute):
        """Cancel older jobs that share the same SO; keep the latest job per SO."""
        from app.services.job_so_guard import cancel_duplicate_so_jobs

        out = cancel_duplicate_so_jobs(dry_run=not execute)
        kept = out.get('kept') or []
        cancelled = out.get('cancelled') or []
        if not kept and not cancelled:
            click.echo('No duplicate SO jobs found.')
            return
        click.echo('Keeping (latest per SO):')
        for row in kept:
            click.echo(f"  SO {row['so_no']} -> job {row['job_no']}")
        label = 'Would cancel' if out.get('dry_run') else 'Cancelled'
        click.echo(f'{label}:')
        for row in cancelled:
            click.echo(f"  job {row['job_no']} (SO {row.get('so_no') or '?'})")
        for row in out.get('errors') or []:
            click.echo(f"  ERROR job {row['job_no']}: {row['error']}", err=True)
        if out.get('dry_run'):
            click.echo('Dry run only. Re-run with --execute to apply.')
