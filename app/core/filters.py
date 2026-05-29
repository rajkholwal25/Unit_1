import json


def fmt_json(value):
    """Pretty-print JSON/dict for templates."""
    if value is None:
        return '—'
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def fmt_seq(value):
    """Format BOM process_sequence (list or comma-separated string)."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ', '.join(str(x).strip() for x in value if str(x).strip())
    return str(value)


def fmt_dt(value):
    if not value:
        return '—'
    try:
        return value.strftime('%Y-%m-%d %H:%M')
    except AttributeError:
        return str(value)


def register_template_filters(app):
    app.template_filter('fmt_json')(fmt_json)
    app.template_filter('fmt_seq')(fmt_seq)
    app.template_filter('fmt_dt')(fmt_dt)
