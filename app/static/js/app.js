/* =============================================================================
   AXIS Trade Flow — Client-Side JavaScript
   =============================================================================
   Minimal JS for form interactions. The application is primarily
   server-rendered via Jinja2 templates.
   ============================================================================= */

document.addEventListener('DOMContentLoaded', () => {
    // Auto-dismiss flash messages after 8 seconds
    document.querySelectorAll('.flash').forEach(flash => {
        setTimeout(() => {
            flash.style.transition = 'opacity 0.3s';
            flash.style.opacity = '0';
            setTimeout(() => flash.remove(), 300);
        }, 8000);
    });

    // Auto-uppercase trade string input
    const tradeInput = document.querySelector('.trade-input');
    if (tradeInput) {
        tradeInput.addEventListener('input', (e) => {
            const pos = e.target.selectionStart;
            e.target.value = e.target.value.toUpperCase();
            e.target.setSelectionRange(pos, pos);
        });
    }
});
