from flask import Flask, request, redirect, url_for, render_template_string, send_file, flash
import sqlite3
import os
import io
import qrcode
from datetime import datetime

app = Flask(__name__)
app.secret_key = "inventory_secret_key"
DB_NAME = "inventory.db"


def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_code TEXT UNIQUE NOT NULL,
        barcode_value TEXT UNIQUE NOT NULL,
        item_name TEXT NOT NULL,
        category TEXT,
        unit TEXT DEFAULT 'pcs',
        default_cost REAL DEFAULT 0,
        default_srp REAL DEFAULT 0,
        reorder_level REAL DEFAULT 0,
        supplier TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_layers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        reference_no TEXT,
        qty_received REAL NOT NULL,
        qty_remaining REAL NOT NULL,
        unit_cost REAL NOT NULL,
        unit_srp REAL DEFAULT 0,
        received_date TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(item_id) REFERENCES items(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        movement_date TEXT NOT NULL,
        movement_type TEXT NOT NULL,
        reference_no TEXT,
        qty_in REAL DEFAULT 0,
        qty_out REAL DEFAULT 0,
        unit_cost REAL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        total_cost REAL DEFAULT 0,
        total_sales REAL DEFAULT 0,
        profit REAL DEFAULT 0,
        remarks TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(item_id) REFERENCES items(id)
    )
    """)

    conn.commit()
    conn.close()


def generate_item_code():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) + 1 AS next_no FROM items")
    next_no = cur.fetchone()["next_no"]
    conn.close()
    return f"ITM-{next_no:06d}"


def get_item_by_barcode(barcode):
    conn = get_conn()
    item = conn.execute("SELECT * FROM items WHERE barcode_value = ?", (barcode,)).fetchone()
    conn.close()
    return item


def get_stock_qty(item_id):
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(qty_remaining), 0) AS qty
        FROM stock_layers
        WHERE item_id = ?
    """, (item_id,)).fetchone()
    conn.close()
    return row["qty"] if row else 0


def compute_dashboard():
    conn = get_conn()

    total_items = conn.execute("SELECT COUNT(*) AS c FROM items WHERE is_active = 1").fetchone()["c"]

    stock_value = conn.execute("""
        SELECT COALESCE(SUM(qty_remaining * unit_cost), 0) AS v
        FROM stock_layers
    """).fetchone()["v"]

    srp_value = conn.execute("""
        SELECT COALESCE(SUM(sl.qty_remaining * COALESCE(i.default_srp, sl.unit_srp)), 0) AS v
        FROM stock_layers sl
        JOIN items i ON i.id = sl.item_id
    """).fetchone()["v"]

    total_sales = conn.execute("""
        SELECT COALESCE(SUM(total_sales), 0) AS v
        FROM inventory_movements
        WHERE movement_type = 'SALE'
    """).fetchone()["v"]

    total_cost = conn.execute("""
        SELECT COALESCE(SUM(total_cost), 0) AS v
        FROM inventory_movements
        WHERE movement_type = 'SALE'
    """).fetchone()["v"]

    total_profit = conn.execute("""
        SELECT COALESCE(SUM(profit), 0) AS v
        FROM inventory_movements
        WHERE movement_type = 'SALE'
    """).fetchone()["v"]

    low_stock = conn.execute("""
        SELECT COUNT(*) AS c
        FROM items i
        WHERE i.is_active = 1
        AND (
            SELECT COALESCE(SUM(qty_remaining), 0)
            FROM stock_layers sl
            WHERE sl.item_id = i.id
        ) <= i.reorder_level
    """).fetchone()["c"]

    conn.close()

    return {
        "total_items": total_items,
        "stock_value": stock_value,
        "srp_value": srp_value,
        "potential_profit": srp_value - stock_value,
        "total_sales": total_sales,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "low_stock": low_stock
    }


BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Inventory System</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        * {
            box-sizing: border-box;
            font-family: Arial, sans-serif;
        }

        body {
            margin: 0;
            background: #f3f6f4;
            color: #1f2937;
        }

        .layout {
            display: flex;
            min-height: 100vh;
        }

        .sidebar {
            width: 240px;
            background: #12372a;
            color: white;
            padding: 20px;
            position: fixed;
            top: 0;
            bottom: 0;
            left: 0;
        }

        .brand {
            font-size: 20px;
            font-weight: bold;
            margin-bottom: 28px;
        }

        .nav a {
            display: block;
            color: #d1fae5;
            text-decoration: none;
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 8px;
        }

        .nav a:hover {
            background: #1f5c45;
        }

        .main {
            margin-left: 240px;
            padding: 24px;
            width: calc(100% - 240px);
        }

        .topbar {
            background: white;
            padding: 18px 22px;
            border-radius: 16px;
            margin-bottom: 20px;
            box-shadow: 0 4px 14px rgba(0,0,0,.05);
        }

        h1 {
            margin: 0;
            font-size: 24px;
        }

        .sub {
            color: #6b7280;
            margin-top: 5px;
        }

        .cards {
            display: grid;
            grid-template-columns: repeat(4, minmax(160px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }

        .card {
            background: white;
            padding: 18px;
            border-radius: 16px;
            box-shadow: 0 4px 14px rgba(0,0,0,.05);
        }

        .card .label {
            color: #6b7280;
            font-size: 13px;
        }

        .card .value {
            font-size: 22px;
            font-weight: bold;
            margin-top: 8px;
        }

        .panel {
            background: white;
            padding: 20px;
            border-radius: 16px;
            box-shadow: 0 4px 14px rgba(0,0,0,.05);
            margin-bottom: 20px;
        }

        .form-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
        }

        label {
            font-size: 13px;
            color: #374151;
            font-weight: bold;
        }

        input, select, textarea {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #d1d5db;
            border-radius: 10px;
            margin-top: 5px;
        }

        button, .btn {
            background: #166534;
            color: white;
            border: 0;
            padding: 10px 14px;
            border-radius: 10px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            font-size: 14px;
        }

        button:hover, .btn:hover {
            background: #14532d;
        }

        .btn-light {
            background: #e5e7eb;
            color: #111827;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        th {
            background: #ecfdf5;
            text-align: left;
            padding: 10px;
            border-bottom: 1px solid #d1d5db;
        }

        td {
            padding: 10px;
            border-bottom: 1px solid #e5e7eb;
        }

        .right {
            text-align: right;
        }

        .flash {
            background: #dcfce7;
            color: #166534;
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 14px;
        }

        .danger {
            color: #b91c1c;
            font-weight: bold;
        }

        .scanner-box {
            max-width: 420px;
            margin-top: 15px;
        }

        @media(max-width: 900px) {
            .sidebar {
                position: relative;
                width: 100%;
                height: auto;
            }

            .layout {
                display: block;
            }

            .main {
                margin-left: 0;
                width: 100%;
            }

            .cards, .form-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>

<body>
<div class="layout">
    <aside class="sidebar">
        <div class="brand">Inventory System</div>
        <div class="nav">
            <a href="{{ url_for('dashboard') }}">Dashboard</a>
            <a href="{{ url_for('items') }}">Item Master</a>
            <a href="{{ url_for('stock_in') }}">Stock In</a>
            <a href="{{ url_for('sale') }}">Sales / Stock Out</a>
            <a href="{{ url_for('scan') }}">Scan</a>
            <a href="{{ url_for('movements') }}">Movement History</a>
        </div>
    </aside>

    <main class="main">
        <div class="topbar">
            <h1>{{ title }}</h1>
            <div class="sub">{{ subtitle }}</div>
        </div>

        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for msg in messages %}
                    <div class="flash">{{ msg }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {{ content|safe }}
    </main>
</div>
</body>
</html>
"""


def page(title, subtitle, content, **kwargs):
    return render_template_string(
        BASE_HTML,
        title=title,
        subtitle=subtitle,
        content=render_template_string(content, **kwargs)
    )


@app.route("/")
def dashboard():
    d = compute_dashboard()

    content = """
    <div class="cards">
        <div class="card"><div class="label">Total Items</div><div class="value">{{ d.total_items }}</div></div>
        <div class="card"><div class="label">Inventory Cost</div><div class="value">₱{{ "{:,.2f}".format(d.stock_value) }}</div></div>
        <div class="card"><div class="label">Inventory at SRP</div><div class="value">₱{{ "{:,.2f}".format(d.srp_value) }}</div></div>
        <div class="card"><div class="label">Potential Profit</div><div class="value">₱{{ "{:,.2f}".format(d.potential_profit) }}</div></div>
    </div>

    <div class="cards">
        <div class="card"><div class="label">Actual Sales</div><div class="value">₱{{ "{:,.2f}".format(d.total_sales) }}</div></div>
        <div class="card"><div class="label">Cost of Sales</div><div class="value">₱{{ "{:,.2f}".format(d.total_cost) }}</div></div>
        <div class="card"><div class="label">Actual Profit</div><div class="value">₱{{ "{:,.2f}".format(d.total_profit) }}</div></div>
        <div class="card"><div class="label">Low Stock Items</div><div class="value">{{ d.low_stock }}</div></div>
    </div>
    """
    return page("Dashboard", "Inventory, sales, and profit overview", content, d=d)


@app.route("/items", methods=["GET", "POST"])
def items():
    conn = get_conn()

    if request.method == "POST":
        item_code = request.form.get("item_code") or generate_item_code()
        barcode_value = item_code

        try:
            conn.execute("""
                INSERT INTO items (
                    item_code, barcode_value, item_name, category, unit,
                    default_cost, default_srp, reorder_level, supplier
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_code,
                barcode_value,
                request.form.get("item_name"),
                request.form.get("category"),
                request.form.get("unit") or "pcs",
                float(request.form.get("default_cost") or 0),
                float(request.form.get("default_srp") or 0),
                float(request.form.get("reorder_level") or 0),
                request.form.get("supplier")
            ))
            conn.commit()
            flash("Item saved successfully.")
        except sqlite3.IntegrityError:
            flash("Item code already exists.")

        conn.close()
        return redirect(url_for("items"))

    rows = conn.execute("""
        SELECT i.*,
        COALESCE((SELECT SUM(qty_remaining) FROM stock_layers sl WHERE sl.item_id = i.id), 0) AS stock_qty
        FROM items i
        WHERE i.is_active = 1
        ORDER BY i.item_name
    """).fetchall()
    conn.close()

    content = """
    <div class="panel">
        <form method="POST">
            <div class="form-grid">
                <div>
                    <label>Item Code</label>
                    <input name="item_code" placeholder="Auto if blank">
                </div>
                <div>
                    <label>Item Name</label>
                    <input name="item_name" required>
                </div>
                <div>
                    <label>Category</label>
                    <input name="category">
                </div>
                <div>
                    <label>Unit</label>
                    <input name="unit" value="pcs">
                </div>
                <div>
                    <label>Default Cost</label>
                    <input name="default_cost" type="number" step="0.01">
                </div>
                <div>
                    <label>Default SRP</label>
                    <input name="default_srp" type="number" step="0.01">
                </div>
                <div>
                    <label>Reorder Level</label>
                    <input name="reorder_level" type="number" step="0.01">
                </div>
                <div>
                    <label>Supplier</label>
                    <input name="supplier">
                </div>
            </div>
            <br>
            <button type="submit">Save Item</button>
        </form>
    </div>

    <div class="panel">
        <table>
            <thead>
                <tr>
                    <th>Item Code</th>
                    <th>Item Name</th>
                    <th>Category</th>
                    <th>Unit</th>
                    <th class="right">Stock</th>
                    <th class="right">Cost</th>
                    <th class="right">SRP</th>
                    <th>QR</th>
                </tr>
            </thead>
            <tbody>
                {% for r in rows %}
                <tr>
                    <td>{{ r.item_code }}</td>
                    <td>{{ r.item_name }}</td>
                    <td>{{ r.category or "" }}</td>
                    <td>{{ r.unit }}</td>
                    <td class="right">{{ "{:,.2f}".format(r.stock_qty) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.default_cost) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.default_srp) }}</td>
                    <td><a class="btn btn-light" href="{{ url_for('qr_code', item_id=r.id) }}" target="_blank">QR</a></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    return page("Item Master", "Create items and generate QR codes", content, rows=rows)


@app.route("/qr/<int:item_id>")
def qr_code(item_id):
    conn = get_conn()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()

    if not item:
        return "Item not found", 404

    img = qrcode.make(item["barcode_value"])
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png", download_name=f"{item['item_code']}.png")


@app.route("/stock-in", methods=["GET", "POST"])
def stock_in():
    conn = get_conn()

    if request.method == "POST":
        barcode = request.form.get("barcode_value")
        item = get_item_by_barcode(barcode)

        if not item:
            flash("Item not found.")
            return redirect(url_for("stock_in"))

        qty = float(request.form.get("qty") or 0)
        unit_cost = float(request.form.get("unit_cost") or 0)
        unit_srp = float(request.form.get("unit_srp") or 0)
        ref = request.form.get("reference_no")
        date = request.form.get("movement_date") or datetime.now().strftime("%Y-%m-%d")
        remarks = request.form.get("remarks")

        if qty <= 0:
            flash("Quantity must be greater than zero.")
            return redirect(url_for("stock_in"))

        conn.execute("""
            INSERT INTO stock_layers (
                item_id, reference_no, qty_received, qty_remaining,
                unit_cost, unit_srp, received_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (item["id"], ref, qty, qty, unit_cost, unit_srp, date))

        conn.execute("""
            INSERT INTO inventory_movements (
                item_id, movement_date, movement_type, reference_no,
                qty_in, unit_cost, unit_price, total_cost, remarks
            )
            VALUES (?, ?, 'IN', ?, ?, ?, ?, ?, ?)
        """, (
            item["id"], date, ref, qty, unit_cost, unit_srp,
            qty * unit_cost, remarks
        ))

        conn.commit()
        conn.close()
        flash("Stock In saved successfully.")
        return redirect(url_for("stock_in"))

    items = conn.execute("SELECT * FROM items WHERE is_active = 1 ORDER BY item_name").fetchall()
    conn.close()

    content = """
    <div class="panel">
        <form method="POST">
            <div class="form-grid">
                <div>
                    <label>Item</label>
                    <select name="barcode_value" required onchange="fillDefaults(this)">
                        <option value="">Select item</option>
                        {% for i in items %}
                        <option value="{{ i.barcode_value }}" data-cost="{{ i.default_cost }}" data-srp="{{ i.default_srp }}">
                            {{ i.item_code }} - {{ i.item_name }}
                        </option>
                        {% endfor %}
                    </select>
                </div>

                <div>
                    <label>Date</label>
                    <input type="date" name="movement_date" value="{{ today }}">
                </div>

                <div>
                    <label>Reference No.</label>
                    <input name="reference_no">
                </div>

                <div>
                    <label>Qty In</label>
                    <input type="number" step="0.01" name="qty" required>
                </div>

                <div>
                    <label>Actual Unit Cost</label>
                    <input type="number" step="0.01" name="unit_cost" id="unit_cost" required>
                </div>

                <div>
                    <label>SRP</label>
                    <input type="number" step="0.01" name="unit_srp" id="unit_srp">
                </div>

                <div style="grid-column: 1 / -1;">
                    <label>Remarks</label>
                    <textarea name="remarks"></textarea>
                </div>
            </div>
            <br>
            <button type="submit">Save Stock In</button>
        </form>
    </div>

    <script>
        function fillDefaults(sel) {
            const opt = sel.options[sel.selectedIndex];
            document.getElementById("unit_cost").value = opt.dataset.cost || 0;
            document.getElementById("unit_srp").value = opt.dataset.srp || 0;
        }
    </script>
    """
    return page("Stock In", "Record incoming inventory with actual cost", content, items=items, today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/sale", methods=["GET", "POST"])
def sale():
    conn = get_conn()

    if request.method == "POST":
        barcode = request.form.get("barcode_value")
        item = get_item_by_barcode(barcode)

        if not item:
            flash("Item not found.")
            return redirect(url_for("sale"))

        qty_needed = float(request.form.get("qty") or 0)
        selling_price = float(request.form.get("selling_price") or 0)
        ref = request.form.get("reference_no")
        date = request.form.get("movement_date") or datetime.now().strftime("%Y-%m-%d")
        remarks = request.form.get("remarks")

        available = get_stock_qty(item["id"])

        if qty_needed <= 0:
            flash("Quantity must be greater than zero.")
            return redirect(url_for("sale"))

        if qty_needed > available:
            flash(f"Insufficient stock. Available only: {available:,.2f}")
            return redirect(url_for("sale"))

        remaining_to_issue = qty_needed
        total_cost = 0

        layers = conn.execute("""
            SELECT *
            FROM stock_layers
            WHERE item_id = ? AND qty_remaining > 0
            ORDER BY received_date ASC, id ASC
        """, (item["id"],)).fetchall()

        for layer in layers:
            if remaining_to_issue <= 0:
                break

            take_qty = min(remaining_to_issue, layer["qty_remaining"])
            cost_part = take_qty * layer["unit_cost"]
            total_cost += cost_part

            conn.execute("""
                UPDATE stock_layers
                SET qty_remaining = qty_remaining - ?
                WHERE id = ?
            """, (take_qty, layer["id"]))

            remaining_to_issue -= take_qty

        total_sales = qty_needed * selling_price
        profit = total_sales - total_cost
        avg_cost_basis = total_cost / qty_needed if qty_needed else 0

        conn.execute("""
            INSERT INTO inventory_movements (
                item_id, movement_date, movement_type, reference_no,
                qty_out, unit_cost, unit_price,
                total_cost, total_sales, profit, remarks
            )
            VALUES (?, ?, 'SALE', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["id"], date, ref, qty_needed, avg_cost_basis, selling_price,
            total_cost, total_sales, profit, remarks
        ))

        conn.commit()
        conn.close()
        flash(f"Sale saved. Profit: ₱{profit:,.2f}")
        return redirect(url_for("sale"))

    items = conn.execute("""
        SELECT i.*,
        COALESCE((SELECT SUM(qty_remaining) FROM stock_layers sl WHERE sl.item_id = i.id), 0) AS stock_qty
        FROM items i
        WHERE i.is_active = 1
        ORDER BY i.item_name
    """).fetchall()
    conn.close()

    content = """
    <div class="panel">
        <form method="POST">
            <div class="form-grid">
                <div>
                    <label>Item</label>
                    <select name="barcode_value" required onchange="fillSaleDefaults(this)">
                        <option value="">Select item</option>
                        {% for i in items %}
                        <option value="{{ i.barcode_value }}" data-srp="{{ i.default_srp }}" data-stock="{{ i.stock_qty }}">
                            {{ i.item_code }} - {{ i.item_name }} | Stock: {{ "{:,.2f}".format(i.stock_qty) }}
                        </option>
                        {% endfor %}
                    </select>
                </div>

                <div>
                    <label>Date</label>
                    <input type="date" name="movement_date" value="{{ today }}">
                </div>

                <div>
                    <label>Sales / Reference No.</label>
                    <input name="reference_no">
                </div>

                <div>
                    <label>Qty Out / Sold</label>
                    <input type="number" step="0.01" name="qty" required>
                </div>

                <div>
                    <label>Selling Price</label>
                    <input type="number" step="0.01" name="selling_price" id="selling_price" required>
                </div>

                <div>
                    <label>Available Stock</label>
                    <input id="available_stock" readonly>
                </div>

                <div style="grid-column: 1 / -1;">
                    <label>Remarks</label>
                    <textarea name="remarks"></textarea>
                </div>
            </div>
            <br>
            <button type="submit">Save Sale / Stock Out</button>
        </form>
    </div>

    <script>
        function fillSaleDefaults(sel) {
            const opt = sel.options[sel.selectedIndex];
            document.getElementById("selling_price").value = opt.dataset.srp || 0;
            document.getElementById("available_stock").value = opt.dataset.stock || 0;
        }
    </script>
    """
    return page("Sales / Stock Out", "Record actual sale and compute FIFO profit", content, items=items, today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/scan")
def scan():
    content = """
    <div class="panel">
        <p>Use this page on cellphone. Allow camera permission, then scan item QR.</p>

        <div class="scanner-box">
            <div id="reader"></div>
        </div>

        <br>

        <div>
            <label>Scanned Code</label>
            <input id="scanned_code" readonly>
        </div>

        <br>

        <a class="btn" id="stockInBtn" href="#">Use for Stock In</a>
        <a class="btn" id="saleBtn" href="#">Use for Sale / Out</a>
    </div>

    <script src="https://unpkg.com/html5-qrcode"></script>
    <script>
        function onScanSuccess(decodedText) {
            document.getElementById("scanned_code").value = decodedText;
            document.getElementById("stockInBtn").href = "/stock-in?barcode=" + encodeURIComponent(decodedText);
            document.getElementById("saleBtn").href = "/sale?barcode=" + encodeURIComponent(decodedText);
        }

        const html5QrCode = new Html5QrcodeScanner(
            "reader",
            { fps: 10, qrbox: 250 },
            false
        );

        html5QrCode.render(onScanSuccess);
    </script>
    """
    return page("Scan", "Scan QR using cellphone camera", content)


@app.route("/movements")
def movements():
    conn = get_conn()
    rows = conn.execute("""
        SELECT m.*, i.item_code, i.item_name
        FROM inventory_movements m
        JOIN items i ON i.id = m.item_id
        ORDER BY m.movement_date DESC, m.id DESC
        LIMIT 200
    """).fetchall()
    conn.close()

    content = """
    <div class="panel">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Reference</th>
                    <th>Item</th>
                    <th class="right">In</th>
                    <th class="right">Out</th>
                    <th class="right">Unit Cost</th>
                    <th class="right">Unit Price</th>
                    <th class="right">Total Cost</th>
                    <th class="right">Sales</th>
                    <th class="right">Profit</th>
                </tr>
            </thead>
            <tbody>
                {% for r in rows %}
                <tr>
                    <td>{{ r.movement_date }}</td>
                    <td>{{ r.movement_type }}</td>
                    <td>{{ r.reference_no or "" }}</td>
                    <td>{{ r.item_code }} - {{ r.item_name }}</td>
                    <td class="right">{{ "{:,.2f}".format(r.qty_in or 0) }}</td>
                    <td class="right">{{ "{:,.2f}".format(r.qty_out or 0) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.unit_cost or 0) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.unit_price or 0) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.total_cost or 0) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.total_sales or 0) }}</td>
                    <td class="right">₱{{ "{:,.2f}".format(r.profit or 0) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    return page("Movement History", "Latest 200 stock and sales transactions", content, rows=rows)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5010)