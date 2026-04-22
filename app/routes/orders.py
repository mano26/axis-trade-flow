{% extends "layouts/base.html" %}
{% block title %}Order #{{ order.ticket_display }}{% endblock %}

{% block content %}
<div class="page-header">
    <h1>Order #{{ order.ticket_display }}</h1>
    <span class="status-badge status-{{ order.status }}">{{ order.status | replace('_', ' ') | upper }}</span>
    {% if order.is_generic %}
    <span class="status-badge" style="background:#fff3e0;color:#e65100;margin-left:4px;">GENERIC</span>
    {% endif %}
</div>

<section class="order-summary">
    <div class="summary-grid">
        <div class="summary-item">
            <label>Trade String</label>
            <span class="trade-string">{{ order.raw_input }}</span>
        </div>
        <div class="summary-item">
            <label>Direction</label>
            <span class="direction-{{ order.direction }}">{{ 'BUY' if order.direction == 'B' else 'SELL' }}</span>
        </div>
        <div class="summary-item">
            <label>Quantity</label>
            <span>{{ order.filled_quantity }} / {{ order.total_quantity }} ({{ order.remaining_quantity }} remaining)</span>
        </div>
        <div class="summary-item">
            <label>Strategy</label>
            <span>{{ order.strategy | upper }}</span>
        </div>
        <div class="summary-item">
            <label>Package Premium</label>
            <span>{{ '%.4f' % order.package_premium if order.package_premium else '' }}</span>
        </div>
        <div class="summary-item">
            <label>Date</label>
            <span>{{ order.trade_date.strftime('%m/%d/%Y') }}</span>
        </div>
        <div class="summary-item">
            <label>Time In</label>
            <span>{{ order.time_in | chicago_time if order.time_in else '' }}</span>
        </div>
        {% if order.fills %}
        <div class="summary-item">
            <label>Fill Time(s)</label>
            <span>
                {% for fill in order.fills %}
                {{ fill.fill_timestamp | chicago_time }}{% if not loop.last %}, {% endif %}
                {% endfor %}
            </span>
        </div>
        {% endif %}
        {% if order.modification_timestamps %}
        <div class="summary-item">
            <label>Modified</label>
            <span>
                {% for ts in order.modification_timestamps %}
                {{ ts | chicago_time }}{% if not loop.last %}, {% endif %}
                {% endfor %}
            </span>
        </div>
        {% endif %}
        <div class="summary-item">
            <label>Time Out</label>
            <span>{{ order.time_out | chicago_time if order.time_out else '—' }}</span>
        </div>
    </div>
</section>

{% if order.status in ('open', 'partial_fill') %}
<section>
    <h2>Record Fill</h2>
    <form method="POST" action="{{ url_for('orders.record_fill', order_id=order.id) }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="inline-form-row">
            <label for="fill_quantity">Fill Qty:</label>
            <input type="number" id="fill_quantity" name="fill_quantity"
                   min="1" max="{{ order.remaining_quantity }}"
                   value="{{ order.remaining_quantity }}"
                   class="fill-qty-input">
            <span class="form-help">of {{ order.remaining_quantity }} remaining</span>
            <button type="submit" class="btn btn-primary">Record Fill</button>
        </div>
    </form>
</section>
{% endif %}

<!-- ================================================================
     Editable Confirmation Legs (using proportional display volumes)
     ================================================================ -->
<section class="legs-section">
    <h2>Confirmation Legs &amp; Prices</h2>

    {% if not order.is_generic %}
    {# ── Parsed order: read-only table, B/S toggles via fetch, price inputs only ── #}
    <table class="legs-table" id="legs-table">
        <thead>
            <tr>
                <th>B/S</th><th>Volume</th><th>Market</th><th>Contract</th>
                <th>Expiry</th><th>Strike</th><th>C/P</th><th>Price</th>
            </tr>
        </thead>
        <tbody>
            {% for leg in display_legs %}
            <tr>
                <td>
                    <button type="button"
                            class="btn-side-toggle btn-side-{{ leg.side }}"
                            data-order-id="{{ order.id }}"
                            data-leg-index="{{ leg.leg_index }}"
                            data-current-side="{{ leg.side }}"
                            title="Click to flip B/S">
                        {{ leg.side }}
                    </button>
                </td>
                <td>{{ leg.volume }}</td>
                <td>{{ leg.market }}</td>
                <td>{{ leg.contract_type }}</td>
                <td>{{ leg.expiry }}</td>
                <td>{{ '%.4f' % leg.strike if leg.strike else '—' }}</td>
                <td>{{ leg.option_type or 'FUT' }}</td>
                <td>
                    {% if order.fills %}
                    <input type="text" name="price_{{ leg.leg_index }}"
                           value="{{ '%.4f' % leg.price if leg.price else '' }}"
                           class="price-input" placeholder="0.0000"
                           form="prices-form"
                           inputmode="decimal" autocomplete="off">
                    {% else %}
                    {{ '%.4f' % leg.price if leg.price else '—' }}
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if order.fills %}
    <form method="POST" action="{{ url_for('orders.save_prices', order_id=order.id) }}" id="prices-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        {% if oldest_pending_fill %}
        <input type="hidden" name="fill_id" value="{{ oldest_pending_fill.id }}">
        {% endif %}
        <div style="margin-top: 8px;">
            <button type="submit" class="btn btn-primary">Save Prices</button>
            {% if oldest_pending_fill and order.fills|length > 1 %}
            <span class="form-help" style="margin-left: 8px;">
                Saving prices for Fill #{{ order.fills.index(oldest_pending_fill) + 1 }}
                ({{ oldest_pending_fill.fill_quantity }} contracts).
            </span>
            {% else %}
            <span class="form-help" style="margin-left: 8px;">Validated against package premium.</span>
            {% endif %}
        </div>
    </form>
    {% endif %}

    {% else %}
    {# ── Generic order: full editable grid with Save Legs ── #}
    <form method="POST" action="{{ url_for('orders.save_legs', order_id=order.id) }}" id="legs-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <table class="legs-table editable-legs-table" id="legs-table">
            <thead>
                <tr>
                    <th>B/S</th><th>Volume</th><th>Market</th><th>Contract</th>
                    <th>Expiry</th><th>Strike</th><th>C/P</th><th>Price</th><th></th>
                </tr>
            </thead>
            <tbody id="legs-tbody">
                {% if display_legs %}
                {% for leg in display_legs %}
                <tr class="leg-row">
                    <td>
                        <select name="leg_side_{{ leg.leg_index }}" class="leg-select">
                            <option value="B" {% if leg.side == 'B' %}selected{% endif %}>B</option>
                            <option value="S" {% if leg.side == 'S' %}selected{% endif %}>S</option>
                        </select>
                    </td>
                    <td><input type="number" name="leg_volume_{{ leg.leg_index }}" value="{{ leg.volume }}" class="leg-num-input"></td>
                    <td>
                        {% if lookups.market %}
                        <select name="leg_market_{{ leg.leg_index }}" class="leg-select">
                            {% for lv in lookups.market %}<option value="{{ lv.value }}" {% if leg.market == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}
                        </select>
                        {% else %}
                        <input type="text" name="leg_market_{{ leg.leg_index }}" value="{{ leg.market }}" class="leg-text-input text-upper">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.contract %}
                        <select name="leg_contract_{{ leg.leg_index }}" class="leg-select">
                            <option value=""></option>{% for lv in lookups.contract %}<option value="{{ lv.value }}" {% if leg.contract_type == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}
                        </select>
                        {% else %}
                        <input type="text" name="leg_contract_{{ leg.leg_index }}" value="{{ leg.contract_type }}" class="leg-text-input text-upper">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.expiry %}
                        <select name="leg_expiry_{{ leg.leg_index }}" class="leg-select">
                            <option value=""></option>{% for lv in lookups.expiry %}<option value="{{ lv.value }}" {% if leg.expiry == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}
                        </select>
                        {% else %}
                        <input type="text" name="leg_expiry_{{ leg.leg_index }}" value="{{ leg.expiry }}" class="leg-text-input text-upper">
                        {% endif %}
                    </td>
                    <td><input type="text" name="leg_strike_{{ leg.leg_index }}" value="{{ '%.4f' % leg.strike if leg.strike else '' }}" class="leg-text-input" placeholder="0.0000"></td>
                    <td>
                        <select name="leg_opttype_{{ leg.leg_index }}" class="leg-select">
                            <option value="">—</option>
                            <option value="C" {% if leg.option_type == 'C' %}selected{% endif %}>C</option>
                            <option value="P" {% if leg.option_type == 'P' %}selected{% endif %}>P</option>
                        </select>
                    </td>
                    <td>
                        <input type="text" name="price_{{ leg.leg_index }}"
                               value="{{ '%.4f' % leg.price if leg.price else '' }}"
                               class="price-input" placeholder="0.0000"
                               form="prices-form"
                               inputmode="decimal" autocomplete="off">
                    </td>
                    <td><button type="button" class="btn-remove" onclick="this.closest('tr').remove()">×</button></td>
                </tr>
                {% endfor %}
                {% else %}
                {% for i in range(6) %}
                <tr class="leg-row">
                    <td><select name="leg_side_{{ i }}" class="leg-select"><option value="B">B</option><option value="S">S</option></select></td>
                    <td><input type="number" name="leg_volume_{{ i }}" value="" class="leg-num-input"></td>
                    <td>
                        {% if lookups.market %}
                        <select name="leg_market_{{ i }}" class="leg-select">{% for lv in lookups.market %}<option value="{{ lv.value }}">{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="leg_market_{{ i }}" value="CME" class="leg-text-input text-upper">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.contract %}
                        <select name="leg_contract_{{ i }}" class="leg-select"><option value=""></option>{% for lv in lookups.contract %}<option value="{{ lv.value }}">{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="leg_contract_{{ i }}" value="" class="leg-text-input text-upper" placeholder="SR3">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.expiry %}
                        <select name="leg_expiry_{{ i }}" class="leg-select"><option value=""></option>{% for lv in lookups.expiry %}<option value="{{ lv.value }}">{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="leg_expiry_{{ i }}" value="" class="leg-text-input text-upper" placeholder="MAR26">
                        {% endif %}
                    </td>
                    <td><input type="text" name="leg_strike_{{ i }}" value="" class="leg-text-input" placeholder="0.0000"></td>
                    <td><select name="leg_opttype_{{ i }}" class="leg-select"><option value="">—</option><option value="C">C</option><option value="P">P</option></select></td>
                    <td><input type="text" name="price_{{ i }}" class="price-input" placeholder="0.0000" form="prices-form"></td>
                    <td><button type="button" class="btn-remove" onclick="this.closest('tr').remove()">×</button></td>
                </tr>
                {% endfor %}
                {% endif %}
            </tbody>
        </table>
        <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;">
            <button type="submit" class="btn btn-primary">Save Legs</button>
            <button type="button" class="btn" onclick="addLegRow()">+ Add Row</button>
        </div>
    </form>

    {% if order.fills %}
    <form method="POST" action="{{ url_for('orders.save_prices', order_id=order.id) }}" id="prices-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        {% if oldest_pending_fill %}
        <input type="hidden" name="fill_id" value="{{ oldest_pending_fill.id }}">
        {% endif %}
        <div style="margin-top: 8px;">
            <button type="submit" class="btn btn-primary">Save Prices</button>
            {% if oldest_pending_fill and order.fills|length > 1 %}
            <span class="form-help" style="margin-left: 8px;">
                Saving prices for Fill #{{ order.fills.index(oldest_pending_fill) + 1 }}
                ({{ oldest_pending_fill.fill_quantity }} contracts).
            </span>
            {% else %}
            <span class="form-help" style="margin-left: 8px;">Validated against package premium.</span>
            {% endif %}
        </div>
    </form>
    {% endif %}
    {% endif %}
</section>

{% if order.fills %}
<section>
    <h2>Counterparties</h2>

    {# ── Allocated fills — read-only with Edit toggle ── #}
    {% for fd in all_fills_display %}
    {% if fd.fill.allocation_status == 'allocated' and fd.counterparties %}
    {% set fill = fd.fill %}
    {% set fidx = loop.index %}
    <div style="margin-bottom: 16px;">
        <p class="form-help" style="margin-bottom: 6px;">
            Fill #{{ fidx }} — {{ fill.fill_quantity }} contracts
            <span class="status-badge status-allocated" style="margin-left: 6px;">ALLOCATED</span>
            <button type="button" class="btn btn-sm" style="margin-left: 10px;"
                    onclick="toggleAmend({{ fill.id }}, this)">Edit</button>
        </p>

        {# Read-only view #}
        <div id="amend-view-{{ fill.id }}">
            {# Prices row #}
            {% if fd.has_prices %}
            <table class="legs-table" style="margin-bottom: 8px;">
                <thead><tr><th>Leg</th><th>Strike</th><th>C/P</th><th>Price</th></tr></thead>
                <tbody>
                {% for lp in fill.leg_prices %}
                {% set leg = order.legs | selectattr("leg_index","equalto",lp.leg_index) | first %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td>{{ "%.4f" % leg.strike if leg and leg.strike else "FUT" }}</td>
                    <td>{{ leg.option_type if leg and leg.option_type else "" }}</td>
                    <td>{{ "%.4f" % lp.price }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
            {% endif %}
            {# Counterparties #}
            <table class="legs-table cp-entry-table">
                <thead>
                    <tr><th>Qty</th><th>Filling Broker</th><th>Counterparty/House</th><th>Bracket</th><th>Notes</th></tr>
                </thead>
                <tbody>
                    {% for cp in fd.counterparties %}
                    <tr>
                        <td>{{ cp.quantity }}</td>
                        <td>{{ cp.broker }}</td>
                        <td>{{ cp.symbol }}</td>
                        <td>{{ cp.bracket }}</td>
                        <td>{{ cp.notes or '' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        {# Combined amendment form — hidden until Edit clicked #}
        <div id="amend-form-{{ fill.id }}" style="display:none; margin-top: 8px;">
        <form method="POST"
              action="{{ url_for('orders.amend_fill', order_id=order.id, fill_id=fill.id) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

            {# House / Account / BK #}
            <div class="summary-grid" style="margin-bottom: 12px;">
                <div class="form-group">
                    <label>House</label>
                    {% if lookups.house %}
                    <select name="house" class="text-upper">
                        <option value="">-- Select --</option>
                        {% for lv in lookups.house %}<option value="{{ lv.value }}" {% if order.house == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}
                    </select>
                    {% else %}
                    <input type="text" name="house" value="{{ order.house or '' }}" class="text-upper">
                    {% endif %}
                </div>
                <div class="form-group">
                    <label>Account</label>
                    {% if lookups.account %}
                    <select name="account" class="text-upper">
                        <option value="">-- Select --</option>
                        {% for lv in lookups.account %}<option value="{{ lv.value }}" {% if order.account == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}
                    </select>
                    {% else %}
                    <input type="text" name="account" value="{{ order.account or '' }}" class="text-upper">
                    {% endif %}
                </div>
                {% if order.has_futures_legs %}
                <div class="form-group">
                    <label>BK Broker</label>
                    <input type="text" name="bk_broker" value="{{ order.bk_broker or '' }}" class="text-upper">
                </div>
                {% endif %}
            </div>

            {# Prices #}
            {% if fd.has_prices %}
            <p class="form-help" style="margin-bottom: 6px; font-weight: 600;">Prices</p>
            <table class="legs-table" style="margin-bottom: 12px;">
                <thead><tr><th>Leg</th><th>Strike</th><th>C/P</th><th>Price</th></tr></thead>
                <tbody>
                {% for leg in order.legs %}
                {% if leg.option_type is not none or leg.strike is not none %}
                {% set existing_price = fill.leg_prices | selectattr("leg_index","equalto",leg.leg_index) | map(attribute="price") | first | default("") %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td>{{ "%.4f" % leg.strike if leg.strike else "FUT" }}</td>
                    <td>{{ leg.option_type or "" }}</td>
                    <td><input type="text" name="price_{{ leg.leg_index }}"
                               value="{{ "%.4f" % existing_price if existing_price != "" else "" }}"
                               class="price-input" style="width:80px;"
                               inputmode="decimal" autocomplete="off"></td>
                </tr>
                {% endif %}
                {% endfor %}
                </tbody>
            </table>
            {% endif %}

            {# Counterparties #}
            <p class="form-help" style="margin-bottom: 6px; font-weight: 600;">Counterparties</p>
            <table class="legs-table cp-entry-table">
                <thead>
                    <tr><th>Qty</th><th>Filling Broker</th><th>Counterparty/House</th><th>Bracket</th><th>Notes</th></tr>
                </thead>
                <tbody>
                    {% for i in range(10) %}
                    {% set cp = fill.counterparties[i] if i < fill.counterparties|length else none %}
                    <tr>
                        <td><input type="number" name="cp_qty_{{ i }}" value="{{ cp.quantity if cp else '' }}" min="0" class="cp-qty-input"></td>
                        <td>
                            {% if lookups.filling_broker %}
                            <select name="cp_broker_{{ i }}" class="text-upper"><option value=""></option>{% for lv in lookups.filling_broker %}<option value="{{ lv.value }}" {% if cp and cp.broker == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                            {% else %}
                            <input type="text" name="cp_broker_{{ i }}" value="{{ cp.broker if cp else '' }}" class="text-upper">
                            {% endif %}
                        </td>
                        <td>
                            {% if lookups.counterparty %}
                            <select name="cp_symbol_{{ i }}" class="text-upper"><option value=""></option>{% for lv in lookups.counterparty %}<option value="{{ lv.value }}" {% if cp and cp.symbol == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                            {% else %}
                            <input type="text" name="cp_symbol_{{ i }}" value="{{ cp.symbol if cp else '' }}" class="text-upper">
                            {% endif %}
                        </td>
                        <td>
                            {% if lookups.bracket %}
                            <select name="cp_bracket_{{ i }}" class="text-upper cp-bracket-input"><option value=""></option>{% for lv in lookups.bracket %}<option value="{{ lv.value }}" {% if cp and cp.bracket == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                            {% else %}
                            <input type="text" name="cp_bracket_{{ i }}" value="{{ cp.bracket if cp else '' }}" maxlength="2" class="text-upper cp-bracket-input">
                            {% endif %}
                        </td>
                        <td><input type="text" name="cp_notes_{{ i }}" value="{{ cp.notes if cp else '' }}" placeholder="Optional"></td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="margin-top: 8px; display: flex; gap: 8px;">
                <button type="submit" class="btn btn-primary">Save Amendment</button>
                <button type="button" class="btn" onclick="toggleAmend({{ fill.id }}, null, true)">Cancel</button>
            </div>
        </form>
        </div>
    </div>
    {% endif %}
    {% endfor %}

    {# ── Entry form for every pending fill, in order ── #}
    {# House/account only shown once on the first pending form #}
    {% set ns = namespace(first_pending=true) %}
    {% for fd in all_fills_display %}
    {% if fd.fill.allocation_status != 'allocated' %}
    {% set fill = fd.fill %}
    {% set fill_num = loop.index %}
    {% set fill_has_prices = fd.has_prices %}
    {% if not fill_has_prices %}
    <p class="form-help" style="color: var(--red); margin-bottom: 12px;">
        Fill #{{ fill_num }} ({{ fill.fill_quantity }} contracts): save leg prices before entering counterparties.
    </p>
    {% endif %}
    <form method="POST" action="{{ url_for('orders.save_counterparties', order_id=order.id) }}"
          id="cp-form-{{ fill.id }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="fill_id" value="{{ fill.id }}">
        {% if ns.first_pending %}
        <div class="summary-grid" style="margin-bottom: 16px;">
            <div class="form-group">
                <label for="house">House</label>
                {% if lookups.house %}
                <select id="house" name="house" class="text-upper">
                    <option value="">-- Select --</option>
                    {% for lv in lookups.house %}
                    <option value="{{ lv.value }}" {% if order.house == lv.value %}selected{% endif %}>{{ lv.value }}</option>
                    {% endfor %}
                </select>
                {% else %}
                <input type="text" id="house" name="house" value="{{ order.house or '' }}" placeholder="House" class="text-upper">
                {% endif %}
            </div>
            <div class="form-group">
                <label for="account">Account</label>
                {% if lookups.account %}
                <select id="account" name="account" class="text-upper">
                    <option value="">-- Select --</option>
                    {% for lv in lookups.account %}
                    <option value="{{ lv.value }}" {% if order.account == lv.value %}selected{% endif %}>{{ lv.value }}</option>
                    {% endfor %}
                </select>
                {% else %}
                <input type="text" id="account" name="account" value="{{ order.account or '' }}" placeholder="Account ID" class="text-upper">
                {% endif %}
            </div>
            {% if order.has_futures_legs %}
            <div class="form-group">
                <label for="bk_broker">BK Broker</label>
                <input type="text" id="bk_broker" name="bk_broker" value="{{ order.bk_broker or '' }}" placeholder="Optional" class="text-upper">
            </div>
            {% endif %}
        </div>
        {% set ns.first_pending = false %}
        {% endif %}
        {% if fill_has_prices %}
        <p class="form-help" style="margin-bottom: 8px;">
            Fill #{{ fill_num }} — Allocate {{ fill.fill_quantity }} contracts. Quantities must sum to {{ fill.fill_quantity }}.
        </p>
        <table class="legs-table cp-entry-table">
            <thead>
                <tr><th>Qty</th><th>Filling Broker</th><th>Counterparty/House</th><th>Bracket</th><th>Notes</th></tr>
            </thead>
            <tbody>
                {% for i in range(10) %}
                {% set cp = fill.counterparties[i] if i < fill.counterparties|length else none %}
                <tr>
                    <td><input type="number" name="cp_qty_{{ i }}" value="{{ cp.quantity if cp else (fill.fill_quantity if i == 0 and not fill.counterparties else '') }}" min="0" class="cp-qty-input"></td>
                    <td>
                        {% if lookups.filling_broker %}
                        <select name="cp_broker_{{ i }}" class="text-upper"><option value=""></option>{% for lv in lookups.filling_broker %}<option value="{{ lv.value }}" {% if cp and cp.broker == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="cp_broker_{{ i }}" value="{{ cp.broker if cp else '' }}" placeholder="Filling Broker" class="text-upper">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.counterparty %}
                        <select name="cp_symbol_{{ i }}" class="text-upper"><option value=""></option>{% for lv in lookups.counterparty %}<option value="{{ lv.value }}" {% if cp and cp.symbol == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="cp_symbol_{{ i }}" value="{{ cp.symbol if cp else '' }}" placeholder="Counterparty/House" class="text-upper">
                        {% endif %}
                    </td>
                    <td>
                        {% if lookups.bracket %}
                        <select name="cp_bracket_{{ i }}" class="text-upper cp-bracket-input"><option value=""></option>{% for lv in lookups.bracket %}<option value="{{ lv.value }}" {% if cp and cp.bracket == lv.value %}selected{% endif %}>{{ lv.value }}</option>{% endfor %}</select>
                        {% else %}
                        <input type="text" name="cp_bracket_{{ i }}" value="{{ cp.bracket if cp else '' }}" placeholder="A" maxlength="2" class="text-upper cp-bracket-input">
                        {% endif %}
                    </td>
                    <td><input type="text" name="cp_notes_{{ i }}" value="{{ cp.notes if cp else '' }}" placeholder="Optional"></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <div style="margin-top: 8px;">
            <button type="button" class="btn btn-primary"
                    onclick="submitCounterparties('cp-form-{{ fill.id }}')">Save Counterparties</button>
            <span class="status-badge status-pending_allocation" style="margin-left: 8px;">PENDING</span>
        </div>
        {% endif %}
    </form>
    {% if not loop.last %}<hr style="margin: 16px 0; border-color: #ddd;">{% endif %}
    {% endif %}
    {% endfor %}
</section>
{% endif %}

<section class="actions-section">
    <h2>Actions</h2>
    <div class="action-buttons">
        {% if order.status == 'open' %}
        <a href="{{ url_for('orders.modify', order_id=order.id) }}" class="btn">Modify Order</a>
        {% endif %}
        {% if order.status == 'partial_fill' %}
        <a href="{{ url_for('orders.modify_balance', order_id=order.id) }}" class="btn">Modify Balance</a>
        {% endif %}
        {% if order.status in ('open', 'partial_fill') %}
        <form method="POST" action="{{ url_for('orders.cancel', order_id=order.id) }}" style="display:inline">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="btn btn-danger" onclick="return confirm('Cancel this order?')">Cancel Order</button>
        </form>
        {% endif %}
        {% if order.status in ('filled', 'partial_fill', 'partial_cancelled') %}
        <a href="{{ url_for('cards.generate', order_id=order.id) }}" class="btn">Print Cards</a>
        <a href="{{ url_for('tickets.generate', order_id=order.id) }}" class="btn">Print Ticket</a>
        <form method="POST" action="{{ url_for('exchange.submit_to_exchange', order_id=order.id) }}" style="display:inline">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="btn btn-primary" onclick="return confirm('Submit to exchange?')">Send to Exchange</button>
        </form>
        {% endif %}
    </div>
</section>

<a href="{{ url_for('orders.index') }}" class="btn btn-back">← Back to Orders</a>
{% endblock %}

{% block scripts %}
<script>
var rowCounter = {{ display_legs | length if display_legs else (6 if order.is_generic else 0) }};

// ── Amendment toggle ──
function toggleAmend(fillId, btn, cancel) {
    var view = document.getElementById('amend-view-' + fillId);
    var form = document.getElementById('amend-form-' + fillId);
    if (!view || !form) return;
    if (cancel) {
        view.style.display = '';
        form.style.display = 'none';
        // restore Edit button text if btn reference was lost — find it
        var btns = document.querySelectorAll('[onclick*="toggleAmend(' + fillId + ',"]');
        btns.forEach(function(b) { if (b.textContent.trim() === 'Cancel') {} else { b.textContent = 'Edit'; } });
        return;
    }
    var isEditing = form.style.display !== 'none';
    if (isEditing) {
        view.style.display = '';
        form.style.display = 'none';
        if (btn) btn.textContent = 'Edit';
    } else {
        view.style.display = 'none';
        form.style.display = '';
        if (btn) btn.textContent = 'Cancel';
    }
}

function addLegRow() {
    var tbody = document.getElementById('legs-tbody');
    var idx = rowCounter;
    var hasFills = {{ 'true' if order.fills else 'false' }};
    var tr = document.createElement('tr');
    tr.className = 'leg-row';
    tr.innerHTML =
        '<td><select name="leg_side_' + idx + '" class="leg-select"><option value="B">B</option><option value="S">S</option></select></td>' +
        '<td><input type="number" name="leg_volume_' + idx + '" value="" class="leg-num-input"></td>' +
        '<td><input type="text" name="leg_market_' + idx + '" value="CME" class="leg-text-input text-upper"></td>' +
        '<td><input type="text" name="leg_contract_' + idx + '" value="" class="leg-text-input text-upper" placeholder="SR3"></td>' +
        '<td><input type="text" name="leg_expiry_' + idx + '" value="" class="leg-text-input text-upper" placeholder="MAR26"></td>' +
        '<td><input type="text" name="leg_strike_' + idx + '" value="" class="leg-text-input" placeholder="0.0000"></td>' +
        '<td><select name="leg_opttype_' + idx + '" class="leg-select"><option value="">—</option><option value="C">C</option><option value="P">P</option></select></td>' +
        '<td><input type="text" name="price_' + idx + '" class="price-input" placeholder="0.0000" form="prices-form"></td>' +
        '<td><button type="button" class="btn-remove" onclick="this.closest(\'tr\').remove()">×</button></td>';
    tbody.appendChild(tr);
    rowCounter++;
}

// ── B/S toggle for parsed order legs ──
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.btn-side-toggle');
    if (!btn) return;
    var orderId = btn.dataset.orderId;
    var legIndex = btn.dataset.legIndex;
    var currentSide = btn.dataset.currentSide;
    var newSide = currentSide === 'B' ? 'S' : 'B';
    var csrfToken = document.querySelector('meta[name="csrf-token"]') ?
        document.querySelector('meta[name="csrf-token"]').content :
        (document.querySelector('input[name="csrf_token"]') ?
         document.querySelector('input[name="csrf_token"]').value : '');

    btn.disabled = true;
    fetch('/orders/' + orderId + '/legs/' + legIndex + '/side', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ side: newSide }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            btn.textContent = data.side;
            btn.dataset.currentSide = data.side;
            btn.className = 'btn-side-toggle btn-side-' + data.side;
        } else {
            alert('Could not update leg: ' + (data.error || 'unknown error'));
        }
        btn.disabled = false;
    })
    .catch(function() {
        alert('Network error updating leg side.');
        btn.disabled = false;
    });
});

function submitCounterparties(formId) {
    var form = document.getElementById(formId || 'cp-form');
    if (!form) return;
    {% if order.has_futures_legs %}
    var bkField = document.getElementById('bk_broker');
    if (bkField && !bkField.value.trim()) {
        if (!confirm('This trade has futures legs but no BK Broker is set.\n\nContinue without BK Broker?')) {
            bkField.focus();
            return;
        }
    }
    {% endif %}
    form.submit();
}
</script>
{% endblock %}