"""Summarize the evidence dump for quick reading."""
import json

d = json.load(open("_evidence_dump.json", encoding="utf-8"))
for case_id in sorted(d):
    rec = d[case_id]
    case = rec["case"]
    primary = case["customer_request"]["claims"][0]["topic"]
    tools = rec["tools"]
    print("=" * 90)
    print(case_id, "| topic:", primary, "| claimed:", case["customer_request"]["claimed_order_id"][:12])

    def get(tool_prefix):
        # find tool entry whose key starts with prefix
        for k, v in tools.items():
            if k.startswith(tool_prefix):
                return v
        return None

    order = get("get_order:")
    if order and "data" in order:
        o = order["data"]
        print("  order.status:", o.get("order_status"),
              "| purchase:", o.get("order_purchase_timestamp"),
              "| delivered_cust:", o.get("order_delivered_customer_date"),
              "| estimated:", o.get("order_estimated_delivery_date"))

    items = get("get_order_items:")
    if items and "data" in items:
        for it in items["data"]:
            print("  item:", it.get("order_item_id"), "price", it.get("price"),
                  "freight", it.get("freight_value"), "seller", it.get("seller_id"),
                  "limit", it.get("shipping_limit_date"))

    pays = get("get_order_payments:")
    if pays and "data" in pays:
        for p in pays["data"]:
            print("  pay: seq", p.get("payment_sequential"), p.get("payment_type"),
                  "installments", p.get("payment_installments"), "value", p.get("payment_value"))

    pt = get("get_payment_timeline:")
    if pt and "data" in pt:
        for e in pt["data"].get("events", []):
            print("  pay-event:", e.get("event_type"), e.get("amount_brl"),
                  e.get("status"), e.get("event_at"))

    rt = get("get_refund_timeline:")
    if rt:
        if "error" in rt:
            print("  refund-timeline: ERROR", rt.get("message"))
        else:
            for e in rt["data"].get("events", []):
                print("  refund-event:", e)

    sh = get("get_shipment_summary:")
    if sh and "data" in sh:
        s = sh["data"]
        print("  shipment.status:", s.get("order_status"),
              "| delivered_cust:", s.get("delivered_customer_at"),
              "| estimated:", s.get("estimated_delivery_at"))
        for e in s.get("events", []):
            print("  ship-event:", e)
        for sl in s.get("shipping_limits", []):
            print("  ship-limit:", sl.get("order_item_id"), sl.get("shipping_limit_at"))

    sellers = get("get_sellers:")
    if sellers and "data" in sellers:
        for s in sellers["data"]:
            print("  seller:", s.get("seller_id"), s.get("seller_city"), s.get("seller_state"))

    prod = get("get_product_context:")
    if prod and "data" in prod:
        for p in prod["data"]:
            print("  product:", p.get("product_id"),
                  p.get("product", {}).get("product_category_name"),
                  "->", p.get("category_name_english"))

    ch = get("get_customer_history:")
    if ch and "data" in ch:
        print("  customer:", ch["data"].get("customer_unique_id"))
        for o in ch["data"].get("orders", []):
            print("  hist-order:", o.get("order_id")[:12], o.get("order_status"),
                  "purchase", o.get("order_purchase_timestamp"),
                  "deliv", o.get("order_delivered_customer_date"))

    pol = get("get_policy:")
    if pol and "data" in pol:
        rule = pol["data"].get("rules", {}).get(primary)
        if rule:
            print("  POLICY rule:", json.dumps(rule, ensure_ascii=False))
