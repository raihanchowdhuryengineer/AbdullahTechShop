# Abdullah's Tech Shop POS — version 2025-10-05 (Sunday)
# -----------------------------------------------
from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime
from pathlib import Path

app = Flask(__name__)
app.secret_key = "supersecret"
DB_PATH = Path("shop.db")

# ---------- DB ----------
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT UNIQUE,
            category TEXT,
            purchase_price REAL NOT NULL,
            selling_price REAL NOT NULL,
            stock INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            date TEXT NOT NULL,
            total_amount REAL NOT NULL,
            total_profit REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            price_each REAL,
            subtotal REAL,
            profit REAL,
            FOREIGN KEY(sale_id) REFERENCES sales(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """)

# ---------- DASHBOARD ----------
@app.route("/")
def dashboard():
    conn = connect()

    # Totals
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    total_stock_value = conn.execute("SELECT IFNULL(SUM(stock * purchase_price),0) FROM products").fetchone()[0]
    totals = conn.execute("SELECT IFNULL(SUM(total_amount),0), IFNULL(SUM(total_profit),0) FROM sales").fetchone()
    total_revenue, total_profit = totals[0], totals[1]

    # Yesterday for percentage comparison
    from datetime import date, timedelta
    today = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    today_sales = conn.execute("SELECT IFNULL(SUM(total_amount),0), IFNULL(SUM(total_profit),0) FROM sales WHERE date LIKE ? || '%'", (today,)).fetchone()
    yesterday_sales = conn.execute("SELECT IFNULL(SUM(total_amount),0), IFNULL(SUM(total_profit),0) FROM sales WHERE date LIKE ? || '%'", (yesterday,)).fetchone()

    def pct_change(today, yesterday):
        if yesterday == 0: return 0
        return round(((today - yesterday) / yesterday) * 100, 1)

    rev_change = pct_change(today_sales[0], yesterday_sales[0])
    profit_change = pct_change(today_sales[1], yesterday_sales[1])

    # Charts: last 7 days revenue
    trend = conn.execute("""
        SELECT SUBSTR(date,1,10) as d, SUM(total_amount) as amt
        FROM sales GROUP BY d ORDER BY d DESC LIMIT 7
    """).fetchall()

    # Top 5 products
    top_products = conn.execute("""
        SELECT p.name, SUM(si.quantity) as qty
        FROM sale_items si
        JOIN products p ON si.product_id = p.id
        GROUP BY p.id ORDER BY qty DESC LIMIT 5
    """).fetchall()

    # Low stock
    low_stock = conn.execute("SELECT name, stock FROM products WHERE stock < 10 ORDER BY stock ASC LIMIT 5").fetchall()

    # Recent transactions
    recent = conn.execute("SELECT id, total_amount, date FROM sales ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()

    now = datetime.now()
    return render_template("dashboard.html",
        total_products=total_products, total_stock_value=total_stock_value,
        total_revenue=total_revenue, total_profit=total_profit,
        rev_change=rev_change, profit_change=profit_change,
        trend=trend[::-1], top_products=top_products,
        low_stock=low_stock, recent=recent,
        date_str=now.strftime("%Y-%m-%d"), day_str=now.strftime("%A"),
        time_str=now.strftime("%I:%M:%S %p"))


# ---------- PRODUCTS ----------
@app.route("/products")
def list_products():
    conn = connect()
    products = conn.execute(
        "SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", products=products)

@app.route("/add", methods=["GET","POST"])
def add_product():
    if request.method == "POST":
        f = request.form
        try:
            with connect() as conn:
                conn.execute("""
                  INSERT INTO products (name,sku,category,purchase_price,selling_price,stock)
                  VALUES (?,?,?,?,?,?)
                """, (f["name"], f["sku"] or None, f["category"],
                      float(f["purchase_price"]), float(f["selling_price"]),
                      int(f["stock"])))
            flash("✅ Product added","success")
            return redirect(url_for("list_products"))
        except sqlite3.IntegrityError:
            flash("⚠️ SKU must be unique","danger")
    return render_template("add_product.html")

@app.route("/edit/<int:pid>", methods=["GET","POST"])
def edit_product(pid):
    conn = connect()
    product = conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    conn.close()
    if not product:
        flash("Not found","danger"); return redirect(url_for("list_products"))
    if request.method == "POST":
        f = request.form
        try:
            with connect() as conn:
                conn.execute("""
                  UPDATE products SET
                  name=?, sku=?, category=?, purchase_price=?, selling_price=?, stock=? WHERE id=?
                """,(f["name"], f["sku"] or None, f["category"],
                     float(f["purchase_price"]), float(f["selling_price"]),
                     int(f["stock"]), pid))
            flash("✅ Updated","success")
            return redirect(url_for("list_products"))
        except sqlite3.IntegrityError:
            flash("⚠️ SKU must be unique","danger")
    return render_template("edit_product.html", product=product)
# ---------- SELL / BILL ----------
@app.route("/sell", methods=["GET", "POST"])
def sell():
    conn = connect()
    products = conn.execute("""
        SELECT id, name, sku, category, purchase_price, selling_price, stock
        FROM products WHERE stock > 0 ORDER BY name
    """).fetchall()

    if request.method == "POST":
        f = request.form
        cname, cphone, caddr = f["customer_name"].strip(), f["customer_phone"].strip(), f["customer_address"].strip()
        items, total_amount, total_profit = [], 0.0, 0.0

        for p in products:
            qty = int(f.get(f"qty_{p['id']}", 0))
            if qty > 0:
                subtotal = p["selling_price"] * qty
                profit = (p["selling_price"] - p["purchase_price"]) * qty
                total_amount += subtotal
                total_profit += profit
                items.append((p["id"], qty, p["selling_price"], subtotal, profit))

        if not items:
            flash("⚠️ No products selected.","warning")
            return redirect(url_for("sell"))

        with connect() as conn2:
            cur = conn2.cursor()
            cur.execute("""
                INSERT INTO sales (customer_name, date, total_amount, total_profit)
                VALUES (?, ?, ?, ?)
            """, (
                f"{cname}|{cphone}|{caddr}",
                datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                total_amount, total_profit
            ))
            sale_id = cur.lastrowid
            for pid, qty, price_each, subtotal, profit in items:
                cur.execute("""
                    INSERT INTO sale_items (sale_id, product_id, quantity, price_each, subtotal, profit)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (sale_id, pid, qty, price_each, subtotal, profit))
                cur.execute("UPDATE products SET stock = stock - ? WHERE id=?", (qty, pid))
            conn2.commit()
        flash("✅ Bill created","success")
        return redirect(url_for("bill", sale_id=sale_id))
    conn.close()
    return render_template("sell.html", products=products)

@app.route("/bill/<int:sale_id>")
def bill(sale_id):
    conn = connect()
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    items = conn.execute("""
        SELECT p.name, p.sku, si.quantity, si.price_each, si.subtotal
        FROM sale_items si JOIN products p ON si.product_id = p.id
        WHERE si.sale_id=?
    """, (sale_id,)).fetchall()
    conn.close()

    # split customer info
    cname=cphone=caddr=""
    if sale["customer_name"]:
        parts=sale["customer_name"].split("|")
        cname=parts[0].strip() if len(parts)>0 else ""
        cphone=parts[1].strip() if len(parts)>1 else ""
        caddr=parts[2].strip() if len(parts)>2 else ""

    return render_template("bill.html",
        sale=sale, items=items,
        customer_name=cname, customer_phone=cphone, customer_address=caddr)

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)

