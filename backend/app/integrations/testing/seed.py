"""Seed data for the fake ERPNext: a small Indian manufacturer's customers, items,
prices, orders, invoices, payments and stock, shaped like ERPNext v16 rows.

`burst` adds that many customers sharing one modified time (a bulk import), which is
where naive paging skips or repeats records. `broken` adds one sales order whose
quantity is not a number, which the adapter must report without failing the page.
"""

import random

from app.integrations.testing.fake_erpnext import FakeERPNext

CUSTOMERS = [
    ("Bharat Forge Components Pvt Ltd", "27AABCB1234F1Z5", "Pune", "Maharashtra", "411018"),
    ("Sundaram Precision Castings Ltd", "33AADCS5678K1ZQ", "Chennai", "Tamil Nadu", "600058"),
    ("Kirloskar Pumps & Valves", "29AAACK9012M1Z2", "Bengaluru", "Karnataka", "560058"),
    ("Godavari Steel Fabricators", "36AAGFG3456P1ZX", "Hyderabad", "Telangana", "500037"),
    ("Anand Auto Electricals", "24AAFFA7890Q1Z1", "Ahmedabad", "Gujarat", "380015"),
    ("Mahindra Agri Implements", "27AAACM2468R1ZA", "Nashik", "Maharashtra", "422007"),
    ("Shakti Hydraulics", "06AAPFS1357T1ZB", "Faridabad", "Haryana", "121003"),
    ("Coromandel Gears Pvt Ltd", "33AABCC8642U1ZC", "Coimbatore", "Tamil Nadu", "641021"),
]
ITEMS = [
    ("GBX-110", "Helical gearbox 110 mm", "Gearboxes", "8483"),
    ("GBX-140", "Helical gearbox 140 mm", "Gearboxes", "8483"),
    ("MTR-7K5", "IE3 motor 7.5 kW", "Motors", "8501"),
    ("MTR-11K", "IE3 motor 11 kW", "Motors", "8501"),
    ("CPL-F50", "Flexible coupling F50", "Couplings", "8483"),
    ("BRG-6205", "Ball bearing 6205", "Bearings", "8482"),
    ("SFT-40", "Machined shaft 40 mm", "Shafts", "8483"),
    ("SPR-KIT", "Gearbox spares kit", "Spares", "8483"),
]
WAREHOUSES = ["Stores - FI", "Finished Goods - FI"]


def seed_demo(fake: FakeERPNext, *, seed: int = 7, burst: int = 0, broken: bool = False) -> None:
    rnd = random.Random(seed)
    customers = []
    for i, (name, gstin, city, state, pin) in enumerate(CUSTOMERS, start=1):
        addr = fake.add("Address", {
            "name": f"{name}-Billing", "address_title": name, "address_type": "Billing",
            "address_line1": f"Plot {10 + i}, MIDC Industrial Area", "address_line2": None,
            "city": city, "state": state, "pincode": pin, "country": "India", "gstin": gstin,
        })
        customers.append(fake.add("Customer", {
            "name": name, "customer_name": name, "customer_type": "Company",
            "customer_group": rnd.choice(["Commercial", "OEM", "Distributor"]),
            # India Compliance keeps GSTIN in its own field; plain ERPNext uses tax_id.
            "territory": "India", "tax_id": gstin if i % 2 else None,
            "gstin": None if i % 2 else gstin,
            "email_id": f"purchase@{name.split()[0].lower()}.example.in", "mobile_no": f"98{i:08d}",
            "website": None, "default_currency": "INR", "payment_terms": "Net 30",
            "customer_primary_address": addr["name"],
        }, credit_limits=[{"company": "Forge Industries", "credit_limit": 500000 * i}]))
    if burst:
        at = fake.tick()
        for j in range(burst):
            fake.add("Customer", {
                "name": f"Imported Customer {j:03d}", "customer_name": f"Imported Customer {j:03d}",
                "customer_type": "Company", "customer_group": "Commercial", "modified": at,
            })

    for code, name, group, hsn in ITEMS:
        fake.add("Item", {
            "name": code, "item_code": code, "item_name": name, "item_group": group,
            "description": f"<div><p>{name}</p></div>", "stock_uom": "Nos",
            "gst_hsn_code": hsn, "is_stock_item": 1, "is_sales_item": 1,
        })
        rate = rnd.randrange(2_000, 90_000, 500)
        fake.add("Item Price", {
            "name": f"IP-{code}-S", "item_code": code, "price_list": "Standard Selling",
            "currency": "INR", "price_list_rate": float(rate), "uom": "Nos", "selling": 1,
            "buying": 0, "valid_from": "2026-04-01",
        })
        fake.add("Item Price", {
            "name": f"IP-{code}-B", "item_code": code, "price_list": "Standard Buying",
            "currency": "INR", "price_list_rate": float(rate * 0.7), "uom": "Nos", "selling": 0,
            "buying": 1, "valid_from": "2026-04-01",
        })
        for wh in WAREHOUSES:
            actual = rnd.randrange(0, 120)
            fake.add("Bin", {
                "name": f"BIN-{code}-{wh[:2]}", "item_code": code, "warehouse": wh,
                "actual_qty": float(actual), "reserved_qty": float(min(actual, 5)),
                "ordered_qty": 0.0, "projected_qty": float(actual - min(actual, 5)),
                "stock_uom": "Nos",
            })

    statuses = [
        ("To Deliver and Bill", 1, 0.0), ("To Deliver and Bill", 1, 40.0), ("To Bill", 1, 100.0),
        ("Completed", 1, 100.0), ("Draft", 0, 0.0), ("Cancelled", 2, 0.0), ("On Hold", 1, 0.0),
        ("Closed", 1, 60.0),
    ]
    for n in range(1, 13):
        cust = customers[n % len(customers)]
        status, docstatus, per_delivered = statuses[n % len(statuses)]
        lines = []
        for code, name, *_rest in rnd.sample(ITEMS, 2):
            qty, rate = rnd.randrange(1, 20), rnd.randrange(2_000, 90_000, 500)
            lines.append({
                "item_code": code, "item_name": name, "description": f"<p>{name}</p>",
                "qty": float(qty), "uom": "Nos", "rate": float(rate), "amount": float(qty * rate),
                "delivered_qty": float(qty) * per_delivered / 100, "delivery_date": "2026-06-30",
                "warehouse": WAREHOUSES[1],
            })
        net = sum(x["amount"] for x in lines)
        so = fake.add("Sales Order", {
            "name": f"SAL-ORD-2026-{n:05d}", "customer": cust["name"],
            "transaction_date": f"2026-05-{n:02d}", "delivery_date": "2026-06-30",
            "po_no": f"PO/{n:04d}", "status": status, "docstatus": docstatus,
            "per_delivered": per_delivered, "currency": "INR", "net_total": net,
            "total_taxes_and_charges": round(net * 0.18, 2), "grand_total": round(net * 1.18, 2),
        }, items=lines)
        if docstatus == 1 and n % 2 == 0:
            total = so["grand_total"]
            paid = [0, total / 2, total][n % 3]
            inv = fake.add("Sales Invoice", {
                "name": f"ACC-SINV-2026-{n:05d}", "customer": cust["name"],
                "posting_date": f"2026-06-{n:02d}", "due_date": f"2026-07-{n:02d}",
                "status": "Paid" if paid == total else ("Overdue" if n % 4 == 0 else "Unpaid"),
                "docstatus": 1, "is_return": 0, "currency": "INR", "net_total": so["net_total"],
                "total_taxes_and_charges": so["total_taxes_and_charges"], "grand_total": total,
                "outstanding_amount": round(total - paid, 2),
            }, items=[{**x, "sales_order": so["name"]} for x in lines])
            if paid:
                fake.add("Payment Entry", {
                    "name": f"ACC-PAY-2026-{n:05d}", "payment_type": "Receive",
                    "party_type": "Customer", "party": cust["name"],
                    "posting_date": f"2026-07-{n:02d}", "paid_amount": paid,
                    "received_amount": paid, "paid_from_account_currency": "INR",
                    "mode_of_payment": "Bank Transfer", "reference_no": f"UTR{n:010d}",
                    "docstatus": 1,
                }, references=[{"reference_doctype": "Sales Invoice",
                                "reference_name": inv["name"], "allocated_amount": paid}])
    # A supplier payment, which must not appear as a receipt.
    fake.add("Payment Entry", {
        "name": "ACC-PAY-2026-09999", "payment_type": "Pay", "party_type": "Supplier",
        "party": "Steel Supplier", "posting_date": "2026-07-01", "paid_amount": 1000.0,
        "paid_from_account_currency": "INR", "docstatus": 1,
    })
    if broken:
        fake.add("Sales Order", {
            "name": "SAL-ORD-2026-BROKEN", "customer": customers[0]["name"],
            "transaction_date": "2026-05-20", "status": "To Deliver and Bill", "docstatus": 1,
            "currency": "INR", "net_total": 10.0, "total_taxes_and_charges": 0.0,
            "grand_total": 10.0,
        }, items=[{"item_code": "GBX-110", "qty": "ten", "rate": 1.0, "amount": 10.0}])
