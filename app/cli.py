"""cli.py — Flask CLI commands for admin operations.

Usage:
    flask seed-processes
    flask sync-sap-customers
    flask sync-sap-items
    flask sync-sap-mirror [--scope all|customers]
"""
import click
from flask import Flask


def register_commands(app: Flask) -> None:
    """Register all custom CLI commands on the app."""

    @app.cli.command('seed-processes')
    def seed_processes():
        """Unit 1 only: EMB, SLT, MET, COT in process_master."""
        from app.services.unit1_processes import seed_unit1_process_master

        click.echo(seed_unit1_process_master())

    @app.cli.command('sync-sap-customers')
    def sync_sap_customers():
        """Pull customers from SAP and update sap_customer_mirror."""
        from app.services.sap_mirror_sync import sync_customers_from_sap

        upserted = sync_customers_from_sap()
        click.echo(f'Synced {upserted} customers from SAP mirror.')

    @app.cli.command('sync-sap-items')
    def sync_sap_items():
        """Pull items from SAP and update sap_item_mirror."""
        from app.services.sap_mirror_sync import sync_items_from_sap

        upserted = sync_items_from_sap()
        click.echo(f'Synced {upserted} items from SAP mirror.')

    @app.cli.command('sync-sap-mirror')
    @click.option(
        '--scope',
        type=click.Choice(['all', 'customers']),
        default='all',
        help='Which SAP mirror tables to refresh from Service Layer.',
    )
    def sync_sap_mirror(scope):
        """Refresh sap_customer_mirror (persistent DB copy)."""
        from flask import current_app

        from app.services.sap_mirror_sync import run_full_mirror_sync

        try:
            out = run_full_mirror_sync(current_app, scope=scope)
        except Exception as e:
            click.echo(f'SAP mirror sync failed: {e}', err=True)
            raise SystemExit(1) from e
        click.echo(f'SAP mirror sync OK: {out}')
