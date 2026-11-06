# =============================================================================
# Per-company page designs
# =============================================================================
# Routes call themed("reports/eod_summary.html") instead of passing the name
# straight to render_template. For a company on the new ConfirmEngine design it
# returns ["ce/reports/eod_summary.html", "reports/eod_summary.html"]; Flask
# renders the first template that exists. For classic companies it returns
# the original name unchanged, so their pages are exactly as before.
#
# New-design page templates live under templates/ce/ and extend the original
# page, overriding only its visible layout. Forms, field names, element IDs
# and page scripts are inherited from the original, so behaviour is shared.
# =============================================================================

from flask import g, has_app_context


def uses_new_design() -> bool:
    if not has_app_context():
        return False
    tenant = getattr(g, "tenant", None)
    return bool(tenant is not None and getattr(tenant, "uses_new_design", False))


def themed(template_name: str):
    if uses_new_design():
        return [f"ce/{template_name}", template_name]
    return template_name
