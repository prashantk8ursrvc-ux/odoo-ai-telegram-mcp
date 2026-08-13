"""
odoo_knowledge_base.py
──────────────────────
Searchable local documentation database for Odoo 18.0 CRM and Enterprise modules.
Provides technical articles, API guidelines, workaround patterns, and troubleshooting guides.
"""

from typing import List, Dict, Any
import re

ARTICLES: List[Dict[str, Any]] = [
    {
        "id": "odoo_18_product_types",
        "title": "Odoo 18.0 Product Types & Category Mapping Rules",
        "keywords": ["product", "type", "consu", "service", "combo", "storable", "replenish"],
        "content": (
            "In Odoo 18.0, the product type selection has changed. The legacy type 'product' (Storable Product) "
            "has been deprecated or merged. The standard valid types for product.template / product.product are now:\n"
            "1. 'consu' (Consumable / Goods): Represents physical products including storable goods.\n"
            "2. 'service': Represents services and non-physical items.\n"
            "3. 'combo': Represents combos/kits.\n\n"
            "WARNING: Creating or writing a product with 'type': 'product' will raise a ValueError. "
            "The MCP server automatically maps 'product' to 'consu' dynamically on write/create. "
            "If an order replenishment rule fails, ensure the product type is set to 'service' or that "
            "inventory locations are properly configured to support replenishment."
        )
    },
    {
        "id": "sales_order_invoicing",
        "title": "Sales Order Invoicing & Remote Invoices Creation Workaround",
        "keywords": ["invoice", "sale_create_invoice", "wizard", "create_invoices", "private", "_create_invoices"],
        "content": (
            "Under Odoo XML-RPC protocols, direct calls to private methods (methods starting with an underscore, "
            "such as `_create_invoices` on `sale.order`) are blocked with Fault 4: 'Private methods cannot be called remotely'.\n\n"
            "WORKAROUND PATTERN:\n"
            "To invoice a Sales Order remotely, instantiate the transient invoicing wizard 'sale.advance.payment.inv' instead:\n"
            "1. Define active context: `ctx = {'active_model': 'sale.order', 'active_ids': [order_id], 'active_id': order_id}`\n"
            "2. Create the wizard record:\n"
            "   `wizard_id = odoo_conn.create('sale.advance.payment.inv', {'advance_payment_method': 'delivered', 'sale_order_ids': [(6, 0, [order_id])]})`\n"
            "3. Call the wizard's public method `create_invoices` passing the wizard ID in args and active context in kwargs.\n"
            "4. Retrieve the newly generated invoice IDs by reading the `invoice_ids` field on the `sale.order` record."
        )
    },
    {
        "id": "pdf_report_rendering",
        "title": "PDF Report Rendering via HTTP Controller Authenticated Fetch",
        "keywords": ["pdf", "report", "download", "_render_qweb_pdf", "print", "render", "actions"],
        "content": (
            "Direct XML-RPC execution of `_render_qweb_pdf` on `ir.actions.report` is blocked because it is a private method.\n\n"
            "WORKAROUND PATTERN:\n"
            "To download a PDF report remotely, fetch it via Odoo's web controller using authenticated HTTP session requests:\n"
            "1. Perform a POST request to `/web/session/authenticate` with json-rpc body containing 'db', 'login', and 'password'.\n"
            "2. Retain the resulting session cookies.\n"
            "3. Issue an HTTP GET request to `/report/pdf/<report_xml_id>/<doc_ids>` using the authenticated session cookies.\n"
            "4. Read the raw response bytes (magic bytes start with '%PDF') and base64-encode them for the client."
        )
    },
    {
        "id": "accounting_payment_registration",
        "title": "Invoice Posting and Payment Registration Workflow",
        "keywords": ["post", "payment", "register", "reconcile", "account.move", "account.payment.register"],
        "content": (
            "Registering payment for an invoice in Odoo requires generating and completing a payment registration wizard:\n"
            "1. Draft Invoices: Set `move_type` to 'out_invoice' (for customer invoice) or 'in_invoice' (for vendor bill).\n"
            "2. Validate Invoice: Call public method `action_post` on the `account.move` record. This changes the state from 'draft' to 'posted' and generates a tax/invoice number.\n"
            "3. Create Payment Register Wizard:\n"
            "   - Context: `ctx = {'active_model': 'account.move', 'active_ids': [invoice_id]}`\n"
            "   - Model: `account.payment.register`\n"
            "   - Values: `{'amount': total_amount, 'journal_id': journal_id}`\n"
            "4. Post Payment: Call public method `action_create_payments` on the wizard record ID with the active context.\n"
            "5. The invoice's `payment_state` will automatically transition to 'paid' or 'in_payment'."
        )
    },
    {
        "id": "fixed_assets_depreciation",
        "title": "Fixed Assets & Depreciation Calculation Guides",
        "keywords": ["asset", "depreciation", "calculation", "account.asset", "board"],
        "content": (
            "Fixed assets in Odoo are managed under the `account.asset` model. When an asset is created, "
            "a depreciation board is generated dynamically to calculate expense lines over its lifetime.\n\n"
            "HEURISTICS:\n"
            "- Linear Depreciation: Depreciation amount per period = (purchase_value - salvage_value) / method_number\n"
            "- Declining Balance Depreciation: Depreciation amount = remaining_book_value * declining_factor\n"
            "- To run depreciation calculations: Read the asset's method parameters, compute the board array of future dates, and verify matching journal entry entries."
        )
    },
    {
        "id": "inventory_eoq_formula",
        "title": "Economic Order Quantity (EOQ) Heuristics & Reorder Rules",
        "keywords": ["eoq", "inventory", "reorder", "orderpoint", "stock", "formula"],
        "content": (
            "Economic Order Quantity (EOQ) is calculated using the Wilson formula:\n"
            "EOQ = sqrt((2 * D * S) / H)\n"
            "Where:\n"
            "- D = Annual demand quantity (usually computed from past sale.order.line volume)\n"
            "- S = Purchase ordering cost (shipping, logistics, administrative costs)\n"
            "- H = Annual holding cost per unit (holding rate * unit cost)\n\n"
            "Use the EOQ tool to suggest optimal batch sizing, and configure `stock.warehouse.orderpoint` "
            "rules automatically to trigger purchase RFQs when stock levels drop below safety parameters."
        )
    },
    {
        "id": "planning_slot_scheduling",
        "title": "Planning Shifts & Scheduling Overlap Checks",
        "keywords": ["planning", "shift", "slot", "overlap", "double-booking", "employee"],
        "content": (
            "Planning slots are stored in the `planning.slot` model. To check for double-booking resource conflicts:\n"
            "1. Read the candidate shift's `start_datetime`, `end_datetime`, and assigned `employee_id`.\n"
            "2. Search for existing slots where:\n"
            "   - `employee_id` matches candidate\n"
            "   - `state` is published or draft\n"
            "   - `start_datetime` is less than candidate `end_datetime`\n"
            "   - `end_datetime` is greater than candidate `start_datetime`\n"
            "3. If any overlapping record is found, report a double-booking validation conflict."
        )
    }
]

def search_articles(query: str) -> List[Dict[str, Any]]:
    """
    Search articles by matching query keywords with article title, keywords, and content.
    Returns articles ordered by match relevance score descending.
    """
    if not query:
        return ARTICLES
        
    query_words = [w.lower() for w in re.findall(r'[a-zA-Z0-9_]+', query) if len(w) > 2]
    if not query_words:
        return ARTICLES[:3] # Default fallback
        
    scored_articles = []
    for article in ARTICLES:
        score = 0
        title_lower = article["title"].lower()
        content_lower = article["content"].lower()
        keywords_lower = [k.lower() for k in article["keywords"]]
        
        for word in query_words:
            # Word match in title (weighted heavily)
            if word in title_lower:
                score += 10
            # Word match in keywords (weighted moderately)
            for kw in keywords_lower:
                if word in kw:
                    score += 5
            # Word match in content
            if word in content_lower:
                score += 1
                
        if score > 0:
            scored_articles.append((score, article))
            
    # Sort by score descending
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_articles]
