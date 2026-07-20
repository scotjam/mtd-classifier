"""Transaction categories aligned to HMRC SA105/SA103 boxes."""

from enum import Enum


class TransactionCategory(str, Enum):
    RENTAL_INCOME = "rental_income"                    # SA105 Box 20a
    PROPERTY_OTHER_INCOME = "property_other_income"     # SA105 Box 20b
    RENT_A_ROOM_INCOME = "rent_a_room_income"           # SA105 Box 20c
    LEASE_PREMIUM_INCOME = "lease_premium_income"       # SA105 Box 22
    REVERSE_PREMIUM_INCOME = "reverse_premium_income"   # SA105 Box 23
    SELF_EMPLOYED_INCOME = "self_employed_income"       # SA103F Box 15
    SE_OTHER_INCOME = "se_other_income"                 # SA103F Box 16
    EMPLOYMENT_INCOME = "employment_income"             # SA102 - final declaration only
    INTEREST_INCOME = "interest_income"                 # SA100 Interest - final declaration only
    DIVIDEND_INCOME = "dividend_income"                 # SA100 Dividends - final declaration only
    CASHBACK = "cashback"                               # Variable/spending cashback - not taxable, reference only
    BANK_REWARDS = "bank_rewards"                       # Fixed monthly account rewards - TAXABLE, SA100 other income
    PROPERTY_REPAIRS = "property_repairs"               # SA105 Box 25
    PROPERTY_INSURANCE = "property_insurance"           # SA105 Box 24
    PROPERTY_SERVICES = "property_services"             # SA105 Box 28
    PROPERTY_TRAVEL = "property_travel"                 # SA105 Box 29b
    LETTING_AGENT_FEES = "letting_agent_fees"           # SA105 Box 27
    PROPERTY_LEGAL_FEES = "property_legal_fees"         # SA105 Box 27
    PROPERTY_OTHER_EXPENSE = "property_other_expense"   # SA105 Box 29a
    PROPERTY_TAX_DEDUCTED = "property_tax_deducted"     # SA105 Box 21 - tax credit, not an expense
    MORTGAGE_INTEREST = "mortgage_interest"             # SA105 Box 44
    NON_RES_FINANCE_COSTS = "non_residential_finance_costs"  # SA105 Box 26
    LOAN_INTEREST = "loan_interest"                     # SA103F Box 25
    SE_GOODS = "se_goods"                               # SA103F Box 17
    SE_SUBCONTRACTORS = "se_subcontractors"             # SA103F Box 18
    SE_STAFF_COSTS = "se_staff_costs"                   # SA103F Box 19
    SE_TRAVEL = "se_travel"                             # SA103F Box 20
    SE_PREMISES = "se_premises"                         # SA103F Box 21
    SE_REPAIRS = "se_repairs"                           # SA103F Box 22
    SE_OFFICE_COSTS = "se_office_costs"                 # SA103F Box 23
    SE_TELECOMS = "se_telecoms"                         # SA103F Box 23
    SE_IT = "se_it"                                     # SA103F Box 23
    SE_PROFESSIONAL_FEES = "se_professional_fees"       # SA103F Box 28
    SE_ADVERTISING = "se_advertising"                   # SA103F Box 24.1
    SE_ENTERTAINMENT = "se_entertainment"               # SA103F Box 24.2 - wholly disallowable
    SE_BANK_CHARGES = "se_bank_charges"                 # SA103F Box 26
    SE_BAD_DEBTS = "se_bad_debts"                       # SA103F Box 27
    SE_DEPRECIATION = "se_depreciation"                 # SA103F Box 29 - wholly disallowable
    SE_OTHER_EXPENSE = "se_other_expense"               # SA103F Box 30
    SE_TAX_DEDUCTED = "se_tax_deducted"                 # SA103F Box 81 - tax credit (CIS), not an expense
    GIFT_AID = "gift_aid"                               # SA100 - final declaration only
    TAX_PAYMENT = "tax_payment"                         # Payments to HMRC - not reported
    PERSONAL = "personal"                               # Not reported
    TRANSFER = "transfer"                               # Inter-account, not reported


# Direction: which categories make sense for money in vs money out. Personal
# and Transfer apply to both. Cross-direction cases (an expense refunded into
# the account, income sent back) are handled by the refund flow, which
# inherits the original transaction's category.
INCOME_CATEGORIES = frozenset({
    TransactionCategory.RENTAL_INCOME,
    TransactionCategory.PROPERTY_OTHER_INCOME,
    TransactionCategory.RENT_A_ROOM_INCOME,
    TransactionCategory.LEASE_PREMIUM_INCOME,
    TransactionCategory.REVERSE_PREMIUM_INCOME,
    TransactionCategory.SELF_EMPLOYED_INCOME,
    TransactionCategory.SE_OTHER_INCOME,
    TransactionCategory.EMPLOYMENT_INCOME,
    TransactionCategory.INTEREST_INCOME,
    TransactionCategory.DIVIDEND_INCOME,
    TransactionCategory.CASHBACK,
    TransactionCategory.BANK_REWARDS,
})
NEUTRAL_CATEGORIES = frozenset({
    TransactionCategory.PERSONAL,
    TransactionCategory.TRANSFER,
    # Both directions: payments to HMRC (money out) and HMRC refunds
    # (money in) - neither taxable income nor a deductible expense.
    TransactionCategory.TAX_PAYMENT,
})
EXPENSE_CATEGORIES = frozenset(
    c for c in TransactionCategory
    if c not in INCOME_CATEGORIES and c not in NEUTRAL_CATEGORIES
)

# Residential mortgage/loan interest on a rental property is NOT a deductible
# property expense since the 2020-21 tax year - it no longer reduces rental
# profit. Instead HMRC gives a tax reduction of 20% (basic rate) of the
# finance cost, capped at the property profit. So this category is excluded
# from the profit calculation entirely; summarise_quarter computes the 20%
# reduction as a separate estimated figure instead. LOAN_INTEREST (a business
# loan, SA103F box 25) is unaffected and stays a normal deductible expense.
FINANCE_COST_CATEGORIES = frozenset({
    TransactionCategory.MORTGAGE_INTEREST,
})

# Tax already deducted at source (SA105 box 21, e.g. the Non-resident
# Landlord Scheme; SA103F box 81, e.g. CIS deductions). Reported to HMRC and
# exported to the bridging templates, but these are TAX CREDITS, not
# allowable expenses - so like the restricted finance costs above they must
# never reduce the profit figure computed from the transactions.
TAX_CREDIT_CATEGORIES = frozenset({
    TransactionCategory.PROPERTY_TAX_DEDUCTED,
    TransactionCategory.SE_TAX_DEDUCTED,
})

# Expense categories that are always wholly disallowable for tax (HMRC
# still wants them in the expense boxes, with the same amount in the
# matching disallowable box, so the net claim is nil): business
# entertainment (SA103F box 24.2/39.2) and depreciation (box 29/44).
# Other expenses can carry a per-transaction disallowable flag instead
# (CategorisedTransaction.disallowable) for e.g. private-use shares.
ALWAYS_DISALLOWABLE_CATEGORIES = frozenset({
    TransactionCategory.SE_ENTERTAINMENT,
    TransactionCategory.SE_DEPRECIATION,
})

# Categories whose bank-deposit total is potentially the WRONG figure for the
# tax return (e.g. net salary vs P60 gross), so the summary/export must show
# them as unconfirmed until the correct figure is explicitly confirmed. See
# services/employment_income.py (GUI/CLI) and models/employment_income.py.
CATEGORIES_REQUIRING_CONFIRMATION = frozenset({
    TransactionCategory.EMPLOYMENT_INCOME,
})

# Excluded from quarterly business totals and bridging files. Gift Aid,
# Employment, Interest and Dividend income are tracked (their yearly totals
# matter for the final declaration - charitable giving relief, SA102, and the
# SA100 interest/dividends pages respectively) but none is self-employment or
# property business activity, so none reaches SA103/SA105 figures. Employment
# income for the tax return should come from the P60/P45, not bank deposits
# (after-tax pay); interest/dividends usually need the bank/registrar's
# certificate, not the bank statement, for the exact taxable figure - these
# categories flag and exclude the deposits, not compute the return figure.
# Cashback is typically not taxable at all and is tracked purely for reference.
NON_REPORTABLE_CATEGORIES = frozenset({
    TransactionCategory.PERSONAL,
    TransactionCategory.TRANSFER,
    TransactionCategory.TAX_PAYMENT,
    TransactionCategory.GIFT_AID,
    TransactionCategory.EMPLOYMENT_INCOME,
    TransactionCategory.INTEREST_INCOME,
    TransactionCategory.DIVIDEND_INCOME,
    TransactionCategory.CASHBACK,
    TransactionCategory.BANK_REWARDS,
})


# What belongs in each category, condensed from HMRC's SA103F/SA105 notes.
# Shown as hover tooltips in the category pickers.
CATEGORY_GUIDANCE: dict["TransactionCategory", str] = {
    TransactionCategory.RENTAL_INCOME:
        "All rents from letting UK property, before expenses - including "
        "amounts charged to tenants for services like cleaning or gardening.",
    TransactionCategory.PROPERTY_OTHER_INCOME:
        "Other property-business receipts: insurance claim proceeds for loss "
        "of rents, grants, way-leaves and similar.",
    TransactionCategory.RENT_A_ROOM_INCOME:
        "Gross rents from letting furnished rooms in YOUR OWN home under the "
        "Rent-a-Room scheme. The exempt amount (box 37) is applied "
        "automatically at export.",
    TransactionCategory.LEASE_PREMIUM_INCOME:
        "Premiums received for granting a lease of up to 50 years.",
    TransactionCategory.REVERSE_PREMIUM_INCOME:
        "Payments or inducements received for taking on a lease.",
    TransactionCategory.PROPERTY_TAX_DEDUCTED:
        "Tax taken off rental income before you received it, e.g. under the "
        "Non-resident Landlord Scheme. A credit against your bill, not an "
        "expense - never reduces profit here.",
    TransactionCategory.PROPERTY_INSURANCE:
        "Rent paid to a superior landlord, business rates, council tax, "
        "water rates, ground rent and buildings/contents insurance for the "
        "let property.",
    TransactionCategory.PROPERTY_REPAIRS:
        "Like-for-like repairs and maintenance: boiler fixes, redecorating, "
        "replacing broken fittings. NOT improvements or extensions - those "
        "are capital.",
    TransactionCategory.PROPERTY_SERVICES:
        "Costs of services provided to tenants, including wages of cleaners "
        "or gardeners you employ.",
    TransactionCategory.PROPERTY_TRAVEL:
        "Car, van and travel costs of running the letting business (e.g. "
        "property visits) - business share only.",
    TransactionCategory.LETTING_AGENT_FEES:
        "Letting and management agents' fees and commissions.",
    TransactionCategory.PROPERTY_LEGAL_FEES:
        "Legal and professional fees of the letting business: tenancy "
        "agreements, accountancy, rent collection. NOT fees for buying or "
        "selling the property (capital).",
    TransactionCategory.PROPERTY_OTHER_EXPENSE:
        "Allowable letting expenses that fit no other box - e.g. stationery, "
        "phone calls, advertising for tenants.",
    TransactionCategory.MORTGAGE_INTEREST:
        "Interest and finance costs on loans for the RESIDENTIAL let "
        "property. Not deducted from profit - HMRC gives a 20% tax "
        "reduction instead (box 44).",
    TransactionCategory.NON_RES_FINANCE_COSTS:
        "Interest and finance costs for NON-residential (commercial) let "
        "property - fully deductible, unlike residential finance costs.",
    TransactionCategory.SELF_EMPLOYED_INCOME:
        "Your turnover: takings, fees, sales and money earned by the "
        "business, before any expenses.",
    TransactionCategory.SE_OTHER_INCOME:
        "Other business income not included in turnover - e.g. equipment "
        "rental income or scrap sales.",
    TransactionCategory.SE_TAX_DEDUCTED:
        "Tax taken off trading income before you received it - typically "
        "CIS deductions. A credit against your bill, not an expense.",
    TransactionCategory.SE_GOODS:
        "Cost of goods bought for resale, or raw materials used.",
    TransactionCategory.SE_SUBCONTRACTORS:
        "Construction industry payments to subcontractors.",
    TransactionCategory.SE_STAFF_COSTS:
        "Wages, salaries, employer NIC, pensions and other staff costs. NOT "
        "your own wages or drawings.",
    TransactionCategory.SE_TRAVEL:
        "Business travel: fuel, fares, vehicle running costs (business "
        "share), hotels and subsistence on business trips. NOT home-to-work "
        "commuting.",
    TransactionCategory.SE_PREMISES:
        "Rent, business rates, power and insurance for business premises. "
        "Working from home: the business share of household bills.",
    TransactionCategory.SE_REPAIRS:
        "Repairs and maintenance of business property and equipment. NOT "
        "improvements (capital).",
    TransactionCategory.SE_OFFICE_COSTS:
        "Phone, fax, stationery, printing, postage and other office costs.",
    TransactionCategory.SE_TELECOMS:
        "Phone and internet costs (business share) - reported in box 23 "
        "with other office costs.",
    TransactionCategory.SE_IT:
        "Software, hosting and small IT costs - reported in box 23. "
        "Computer/equipment purchases are usually capital allowances "
        "instead.",
    TransactionCategory.SE_PROFESSIONAL_FEES:
        "Accountancy, legal and other professional fees for the business. "
        "NOT fees for buying property/equipment (capital) or personal tax "
        "work.",
    TransactionCategory.SE_ADVERTISING:
        "Advertising and marketing: ads, business websites, mailshots, free "
        "samples.",
    TransactionCategory.SE_ENTERTAINMENT:
        "Entertaining clients or suppliers, and business gifts. WHOLLY "
        "DISALLOWABLE for tax - the matching disallowable entry is added "
        "automatically.",
    TransactionCategory.LOAN_INTEREST:
        "Interest on bank and other business loans, and alternative finance "
        "payments.",
    TransactionCategory.SE_BANK_CHARGES:
        "Bank, overdraft and credit card charges, hire purchase interest "
        "and leasing payments for the business.",
    TransactionCategory.SE_BAD_DEBTS:
        "Amounts included in turnover that will definitely never be paid. "
        "NOT general provisions.",
    TransactionCategory.SE_DEPRECIATION:
        "Depreciation and losses/profits on sales of assets. WHOLLY "
        "DISALLOWABLE - claim capital allowances instead; the disallowable "
        "entry is added automatically.",
    TransactionCategory.SE_OTHER_EXPENSE:
        "Allowable business costs that fit no other box - trade journals, "
        "subscriptions, protective clothing.",
    TransactionCategory.EMPLOYMENT_INCOME:
        "Salary deposits. HMRC already gets this via PAYE; it is never in "
        "quarterly updates. Confirm the gross figure (P60/payslips) so the "
        "app's tax estimates are right.",
    TransactionCategory.INTEREST_INCOME:
        "Bank and building society interest. A year-end (SA100) item - use "
        "the bank's interest certificate for the return figure.",
    TransactionCategory.DIVIDEND_INCOME:
        "Company dividends. A year-end (SA100) item - use dividend vouchers "
        "for the return figure.",
    TransactionCategory.CASHBACK:
        "VARIABLE cashback only: card cashback earned on personal spending, "
        "and bank switch bonuses - not taxable (HMRC treats them as "
        "discounts/inducements). NOT for fixed monthly account rewards - "
        "those are taxable, use Bank Rewards. Cashback on business "
        "purchases should reduce the expense claimed instead.",
    TransactionCategory.BANK_REWARDS:
        "Fixed monthly 'reward' payments from a bank account (e.g. a "
        "monthly loyalty payment for paying in a salary or direct debits). "
        "TAXABLE as annual payments, usually paid net of 20% basic-rate "
        "tax - higher-rate taxpayers owe the difference. Declared as other "
        "UK income on the year-end final declaration.",
    TransactionCategory.GIFT_AID:
        "Charitable donations made under Gift Aid - claimed on the final "
        "declaration (they widen your basic-rate band). Not a business "
        "expense.",
    TransactionCategory.TAX_PAYMENT:
        "Income tax, NIC and payments on account paid to HMRC (or refunds "
        "received). Not a business expense and not taxable income.",
    TransactionCategory.PERSONAL:
        "Private, non-business spending or income in a mixed account - "
        "excluded from every figure and export.",
    TransactionCategory.TRANSFER:
        "Money moved between your own accounts - not income or expense.",
}


def category_group(cat: "TransactionCategory") -> str:
    """Which picker group a category belongs to: "SA103" (self-employment
    quarterly boxes), "SA105" (UK property quarterly boxes), or "OTHER"
    (year-end / final-declaration / not-reported categories)."""
    box = CATEGORY_BOX_MAPPING.get(cat, "")
    if box.startswith("SA103"):
        return "SA103"
    if box.startswith("SA105"):
        return "SA105"
    return "OTHER"


def rent_a_room_limit(tax_year: int) -> float:
    """The Rent-a-Room scheme's annual exempt amount (SA105 box 37).

    £7,500 since 2016-17 (£4,250 before). HALVED to £3,750 when someone
    else also receives income from letting the same property - the app
    can't know that, so callers surface a "halve it if shared" note
    wherever the auto-filled figure is shown.
    """
    return 7500.0 if tax_year >= 2016 else 4250.0


def categories_for_amount(amount: float) -> list[TransactionCategory]:
    """Categories applicable to a transaction of this direction, in enum order."""
    base = INCOME_CATEGORIES if amount >= 0 else EXPENSE_CATEGORIES
    allowed = base | NEUTRAL_CATEGORIES
    return [c for c in TransactionCategory if c in allowed]


# Maps categories to their HMRC SA form box numbers (SA103F full form / SA105,
# matching the My Tax Digital bridging templates and current paper forms)
CATEGORY_BOX_MAPPING: dict[TransactionCategory, str] = {
    TransactionCategory.RENTAL_INCOME: "SA105 Box 20a - Total rents and other income from property",
    TransactionCategory.PROPERTY_OTHER_INCOME: "SA105 Box 20b - Other income from property",
    TransactionCategory.RENT_A_ROOM_INCOME: "SA105 Box 20c - Total rents received from rent-a-room properties",
    TransactionCategory.LEASE_PREMIUM_INCOME: "SA105 Box 22 - Premiums for the grant of a lease",
    TransactionCategory.REVERSE_PREMIUM_INCOME: "SA105 Box 23 - Reverse premiums and inducements",
    TransactionCategory.SELF_EMPLOYED_INCOME: "SA103F Box 15 - Turnover",
    TransactionCategory.SE_OTHER_INCOME: "SA103F Box 16 - Any other business income not included in box 15",
    TransactionCategory.SE_TAX_DEDUCTED: "SA103F Box 81 - Tax taken off trading income (a tax credit, e.g. CIS - does not reduce profit)",
    TransactionCategory.EMPLOYMENT_INCOME: "SA102 Employment - use P60/P45 figures, not bank deposits, for the tax return",
    TransactionCategory.INTEREST_INCOME: "SA100 Interest - use your bank's interest certificate, not bank deposits, for the tax return",
    TransactionCategory.DIVIDEND_INCOME: "SA100 Dividends - use your registrar/broker's dividend voucher, not bank deposits, for the tax return",
    TransactionCategory.CASHBACK: "Usually not taxable - VARIABLE, spending-based cashback and bank switch bonuses. Fixed monthly account rewards are taxable: use Bank Rewards. Cashback on BUSINESS purchases should reduce the expense claimed",
    TransactionCategory.BANK_REWARDS: "SA100 other UK income - fixed monthly account 'reward' payments are TAXABLE annual payments (usually paid net of 20% basic-rate tax); declare at the final declaration, not in quarterly updates",
    TransactionCategory.PROPERTY_REPAIRS: "SA105 Box 25 - Property repairs and maintenance",
    TransactionCategory.PROPERTY_INSURANCE: "SA105 Box 24 - Rent, rates, insurance and ground rents",
    TransactionCategory.LETTING_AGENT_FEES: "SA105 Box 27 - Legal, management and other professional fees",
    TransactionCategory.PROPERTY_LEGAL_FEES: "SA105 Box 27 - Legal, management and other professional fees",
    TransactionCategory.PROPERTY_OTHER_EXPENSE: "SA105 Box 29a - Other allowable property expenses",
    TransactionCategory.PROPERTY_SERVICES: "SA105 Box 28 - Costs of services provided, including wages",
    TransactionCategory.PROPERTY_TRAVEL: "SA105 Box 29b - Car, van and travel costs",
    TransactionCategory.PROPERTY_TAX_DEDUCTED: "SA105 Box 21 - Tax already deducted from the rental income (a tax credit - does not reduce profit)",
    TransactionCategory.MORTGAGE_INTEREST: "SA105 Box 44 - Residential property finance costs (restricted)",
    TransactionCategory.NON_RES_FINANCE_COSTS: "SA105 Box 26 - Non-residential property finance costs",
    TransactionCategory.LOAN_INTEREST: "SA103F Box 25 - Interest on bank and other loans",
    TransactionCategory.SE_GOODS: "SA103F Box 17 - Cost of goods bought for resale or goods used",
    TransactionCategory.SE_SUBCONTRACTORS: "SA103F Box 18 - Construction industry - payments to subcontractors",
    TransactionCategory.SE_STAFF_COSTS: "SA103F Box 19 - Wages, salaries and other staff costs",
    TransactionCategory.SE_TRAVEL: "SA103F Box 20 - Car, van and travel expenses",
    TransactionCategory.SE_PREMISES: "SA103F Box 21 - Rent, rates, power and insurance costs",
    TransactionCategory.SE_REPAIRS: "SA103F Box 22 - Repairs and maintenance of property and equipment",
    TransactionCategory.SE_OFFICE_COSTS: "SA103F Box 23 - Phone, fax, stationery and other office costs",
    TransactionCategory.SE_TELECOMS: "SA103F Box 23 - Phone, fax, stationery and other office costs",
    TransactionCategory.SE_IT: "SA103F Box 23 - Phone, fax, stationery and other office costs",
    TransactionCategory.SE_PROFESSIONAL_FEES: "SA103F Box 28 - Accountancy, legal and other professional fees",
    TransactionCategory.SE_ADVERTISING: "SA103F Box 24.1 - Advertising costs",
    TransactionCategory.SE_ENTERTAINMENT: "SA103F Box 24.2 - Business entertainment costs (wholly disallowable - reported with a matching disallowable entry)",
    TransactionCategory.SE_BANK_CHARGES: "SA103F Box 26 - Bank, credit card and other financial charges",
    TransactionCategory.SE_BAD_DEBTS: "SA103F Box 27 - Irrecoverable debts written off",
    TransactionCategory.SE_DEPRECIATION: "SA103F Box 29 - Depreciation and loss/profit on sale of assets (wholly disallowable - reported with a matching disallowable entry)",
    TransactionCategory.SE_OTHER_EXPENSE: "SA103F Box 30 - Other business expenses",
    TransactionCategory.GIFT_AID: "SA100 Gift Aid - claim in final declaration, not quarterly updates",
    TransactionCategory.TAX_PAYMENT: "Not reported - personal tax paid to HMRC (income tax, NIC, payments on account) is not a deductible expense; refunds are not income",
    TransactionCategory.PERSONAL: "Not reported",
    TransactionCategory.TRANSFER: "Not reported",
}

# Human-readable labels for interactive display
CATEGORY_LABELS: dict[TransactionCategory, str] = {
    TransactionCategory.RENTAL_INCOME: "Rental Income",
    TransactionCategory.PROPERTY_OTHER_INCOME: "Property Other Income",
    TransactionCategory.RENT_A_ROOM_INCOME: "Rent-a-Room Income",
    TransactionCategory.LEASE_PREMIUM_INCOME: "Lease Premium (grant of lease)",
    TransactionCategory.REVERSE_PREMIUM_INCOME: "Reverse Premium / Inducement",
    TransactionCategory.SELF_EMPLOYED_INCOME: "Self-Employed Income",
    TransactionCategory.SE_OTHER_INCOME: "SE Other Income",
    TransactionCategory.EMPLOYMENT_INCOME: "Employment Income",
    TransactionCategory.INTEREST_INCOME: "Interest Income",
    TransactionCategory.DIVIDEND_INCOME: "Dividend Income",
    TransactionCategory.CASHBACK: "Variable Cashback",
    TransactionCategory.BANK_REWARDS: "Bank Rewards (fixed monthly)",
    TransactionCategory.PROPERTY_REPAIRS: "Property Repairs",
    TransactionCategory.PROPERTY_INSURANCE: "Property Rent, Rates & Insurance",
    TransactionCategory.PROPERTY_SERVICES: "Property Services & Wages",
    TransactionCategory.PROPERTY_TRAVEL: "Property Travel",
    TransactionCategory.LETTING_AGENT_FEES: "Letting Agent Fees",
    TransactionCategory.PROPERTY_LEGAL_FEES: "Property Legal Fees",
    TransactionCategory.PROPERTY_OTHER_EXPENSE: "Property Other Expense",
    TransactionCategory.PROPERTY_TAX_DEDUCTED: "Tax Deducted from Rent",
    TransactionCategory.MORTGAGE_INTEREST: "Mortgage Interest",
    TransactionCategory.NON_RES_FINANCE_COSTS: "Non-Residential Finance Costs",
    TransactionCategory.LOAN_INTEREST: "Loan Interest",
    TransactionCategory.SE_GOODS: "SE Goods for Resale",
    TransactionCategory.SE_SUBCONTRACTORS: "SE Subcontractors (CIS)",
    TransactionCategory.SE_STAFF_COSTS: "SE Staff Costs",
    TransactionCategory.SE_TRAVEL: "SE Travel",
    TransactionCategory.SE_PREMISES: "SE Premises (rent, rates, power)",
    TransactionCategory.SE_REPAIRS: "SE Repairs & Maintenance",
    TransactionCategory.SE_OFFICE_COSTS: "SE Office Costs",
    TransactionCategory.SE_TELECOMS: "SE Telecoms",
    TransactionCategory.SE_IT: "SE IT",
    TransactionCategory.SE_PROFESSIONAL_FEES: "SE Professional Fees",
    TransactionCategory.SE_ADVERTISING: "SE Advertising",
    TransactionCategory.SE_ENTERTAINMENT: "SE Business Entertainment",
    TransactionCategory.SE_BANK_CHARGES: "SE Bank & Card Charges",
    TransactionCategory.SE_BAD_DEBTS: "SE Bad Debts Written Off",
    TransactionCategory.SE_DEPRECIATION: "SE Depreciation / Asset Sales",
    TransactionCategory.SE_OTHER_EXPENSE: "SE Other Expense",
    TransactionCategory.SE_TAX_DEDUCTED: "SE Tax Deducted (CIS)",
    TransactionCategory.GIFT_AID: "Gift Aid Donation",
    TransactionCategory.TAX_PAYMENT: "Tax Payment (HMRC)",
    TransactionCategory.PERSONAL: "Personal",
    TransactionCategory.TRANSFER: "Transfer",
}
