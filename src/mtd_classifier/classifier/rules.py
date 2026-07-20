"""Built-in default classification rules."""

from __future__ import annotations

# Each rule is (regex_pattern, category)
# These are applied in order; first match wins within the pattern tier.
BUILTIN_PATTERNS: list[tuple[str, str]] = [
    # --- Rental / Property ---
    (r"(?i)\bmortgage\b", "mortgage_interest"),
    (r"(?i)\bletting\s+agent", "letting_agent_fees"),
    (r"(?i)\brent\s*(received|income|payment\s+from)", "rental_income"),
    (r"(?i)\btenant", "rental_income"),
    (r"(?i)\bproperty\s+insurance", "property_insurance"),
    (r"(?i)\bbuildings?\s+insurance", "property_insurance"),
    (r"(?i)\blandlord\s+insurance", "property_insurance"),
    (r"(?i)\brepair", "property_repairs"),
    (r"(?i)\bplumb(er|ing)\b", "property_repairs"),
    (r"(?i)\belectrician\b", "property_repairs"),
    (r"(?i)\bdecorat", "property_repairs"),
    (r"(?i)\bgas\s+safe", "property_repairs"),
    (r"(?i)\bboiler\b", "property_repairs"),
    (r"(?i)\bconveyancing\b", "property_legal_fees"),
    (r"(?i)\bsolicitor", "property_legal_fees"),
    # SA105 box 24 is "Rent, rates, insurance and ground rents" - HMRC's
    # notes put ground rent, service charges, council tax and water rates
    # there, not in the box 29a catch-all.
    (r"(?i)\bground\s+rent\b", "property_insurance"),
    (r"(?i)\bservice\s+charge\b", "property_insurance"),
    (r"(?i)\bcouncil\s+tax\b", "property_insurance"),
    (r"(?i)\bwater\s+rates?\b", "property_insurance"),

    # --- Self-Employed ---
    (r"(?i)\binvoice\b", "self_employed_income"),
    (r"(?i)\bfreelance\b", "self_employed_income"),
    (r"(?i)\bconsulting\s+fee", "self_employed_income"),
    (r"(?i)\baccountant", "se_professional_fees"),
    # Payments to (or refunds from) HMRC are the tax itself - neither a
    # deductible expense nor taxable income.
    (r"(?i)\bhmrc\b", "tax_payment"),
    (r"(?i)\btax\s+return\b", "se_professional_fees"),
    (r"(?i)\btrain\s+ticket\b", "se_travel"),
    (r"(?i)\brailway\b", "se_travel"),
    (r"(?i)\bnational\s+rail\b", "se_travel"),
    (r"(?i)\buber\b(?!.*eats)", "se_travel"),
    (r"(?i)\bparking\b", "se_travel"),
    (r"(?i)\badvertis", "se_advertising"),
    (r"(?i)\bgoogle\s+ads\b", "se_advertising"),
    (r"(?i)\bfacebook\s+ads\b", "se_advertising"),
    (r"(?i)\boffice\s+supplies\b", "se_office_costs"),
    (r"(?i)\bstationery\b", "se_office_costs"),
    (r"(?i)\bprinter\b", "se_office_costs"),

    # --- Transfers ---
    (r"(?i)\btransfer\s+(to|from)\b", "transfer"),
    (r"(?i)\binter\s*account\b", "transfer"),
    (r"(?i)\bstanding\s+order\s+(to|from)\s+.*savings", "transfer"),

    # --- Bank incentives ---
    # Fixed monthly account rewards are TAXABLE annual payments (SA100
    # other income); variable spending cashback and switch bonuses are not.
    (r"(?i)\breward\s+payment\b", "bank_rewards"),
    (r"(?i)\bmonthly\s+reward\b", "bank_rewards"),
    (r"(?i)\bcashback\b", "cashback"),
    (r"(?i)\bswitch\s+(bonus|incentive|offer)\b", "cashback"),

    # --- Personal (common UK merchants) ---
    (r"(?i)\btesco\b", "personal"),
    (r"(?i)\bsainsbury", "personal"),
    (r"(?i)\basda\b", "personal"),
    (r"(?i)\bmorrisons\b", "personal"),
    (r"(?i)\bwaitrose\b", "personal"),
    (r"(?i)\baldo\b", "personal"),
    (r"(?i)\blidl\b", "personal"),
    (r"(?i)\bnetflix\b", "personal"),
    (r"(?i)\bspotify\b", "personal"),
    (r"(?i)\bamazon\s+prime\b", "personal"),
    (r"(?i)\bdisney\s*\+", "personal"),
    (r"(?i)\bcosta\b", "personal"),
    (r"(?i)\bstarbucks\b", "personal"),
    (r"(?i)\bpret\b", "personal"),
    (r"(?i)\bmcdonalds\b", "personal"),
    (r"(?i)\bnandos\b", "personal"),
    (r"(?i)\batm\b", "personal"),
    (r"(?i)\bcash\s+withdrawal\b", "personal"),
]
