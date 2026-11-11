import pandas as pd

SAGE_COLUMNS = [
    "Credit Card",
    "Include",
    "Transaction #",
    "Description",
    "Payee",
    "Charge Amount",
    "Credit Amount",
    "Posted Date",
    "Notes",
    "Account",
    "Subaccount",
    "Job",
    "Phase",
    "Job Cost Code",
    "Job Cost Type",
    "Equipment",
    "Equipment Cost Code",
    "Equipment Cost Type",
]


def first_value(transaction, *keys):
    """
    Return the first non-empty value from the supplied keys.
    """
    for key in keys:
        value = transaction.get(key)

        if value is not None and str(value).strip():
            return value

    return None


def format_transaction_number(value):
    """
    Sage transaction number format:
    YYYY-MM-DDTHH:MM:SS
    """
    if not value:
        return None

    try:
        return pd.to_datetime(value).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return str(value).strip()


def format_posted_date(value):
    """
    Format date for Sage.
    """
    if not value:
        return None

    try:
        return pd.to_datetime(value).strftime("%m/%d/%Y")
    except Exception:
        return str(value).strip()


def normalize_job(value):
    """
    BILL budgetName should look like:
    1234 - Job Name

    If it does not, leave Job blank.
    """
    if not value:
        return None

    value = str(value).strip()

    parts = value.split(" - ", 1)

    if len(parts) != 2:
        return None

    if not parts[0].strip().isdigit():
        return None

    return value


def build_description(transaction):
    """
    Combine employee/user name with BILL notes.
    """
    user_name = transaction.get("userName")
    notes = transaction.get("Notes")

    user_name = str(user_name).strip() if user_name else ""
    notes = str(notes).strip() if notes else ""

    if user_name and notes:
        return f"{user_name} - {notes}"

    if user_name:
        return user_name

    if notes:
        return notes

    return None


def format_bill_transactions(transactions):

    rows = []

    for transaction in transactions:

        # Transaction number
        transaction_num = first_value(
            transaction,
            "updatedTime",
            "submittedTime",
            "occurredTime",
            "occurredDate",
        )

        transaction_num = format_transaction_number(transaction_num)

        # Posted date
        posted_date = first_value(
            transaction,
            "authorizedTime",
            "occurredTime",
            "occurredDate",
            "submittedTime",
        )

        if not posted_date:
            posted_date = transaction.get("Cleared Time in Statement")

        posted_date = format_posted_date(posted_date)

        # Amount
        amount = transaction.get("amount")

        if amount is not None:
            try:
                amount = float(amount)
            except (ValueError, TypeError):
                pass

        if amount is None:
            charge = None
            credit = None
        elif amount < 0:
            credit = abs(amount)
            charge = None
        else:
            charge = amount
            credit = None

        notes = transaction.get("Notes")

        # Sage job
        job = normalize_job(transaction.get("budgetName"))

        # Job cost fields
        job_cost_code = transaction.get("2 - Sage Job Cost Codes")

        job_cost_type = transaction.get("3 - Sage Cost Types")

        # Match existing BSE rule:
        # no cost code = no cost type
        if not job_cost_code:
            job_cost_type = None

        rows.append(
            {
                "Credit Card": "1 - Bill Spend & Expense",
                "Include": "Include",
                "Transaction #": transaction_num,
                "Description": build_description(transaction),
                "Payee": transaction.get("merchantName"),
                "Charge Amount": charge,
                "Credit Amount": credit,
                "Posted Date": posted_date,
                "Notes": notes,
                "Account": transaction.get("Sage General Ledger Account"),
                "Subaccount": transaction.get("Sage Vehicle and Equipment List"),
                "Job": job,
                "Phase": None,
                "Job Cost Code": job_cost_code,
                "Job Cost Type": job_cost_type,
                "Equipment": None,
                "Equipment Cost Code": None,
                "Equipment Cost Type": None,
            }
        )

    return pd.DataFrame(
        rows,
        columns=SAGE_COLUMNS,
    )


def format_bill_reimbursements(reimbursements, reference_data):

    # Build employee lookup
    user_lookup = {}

    for user in reference_data["users"]:

        first_name = user.get("firstName") or ""
        last_name = user.get("lastName") or ""

        full_name = f"{first_name} {last_name}".strip()

        for key in ("uuid", "id"):
            if user.get(key):
                user_lookup[str(user[key])] = full_name

    # Build budget lookup
    budget_lookup = {}

    for budget in reference_data["budgets"]:

        budget_name = budget.get("name")

        for key in ("uuid", "id"):
            if budget.get(key):
                budget_lookup[str(budget[key])] = budget_name

    rows = []

    for reimbursement in reimbursements:

        user_id = reimbursement.get("userUuid") or reimbursement.get("userId")

        budget_id = reimbursement.get("budgetUuid") or reimbursement.get("budgetId")

        employee_name = user_lookup.get(str(user_id))
        budget_name = budget_lookup.get(str(budget_id))

        row = {
            "Reimbursement ID": (reimbursement.get("uuid") or reimbursement.get("id")),
            "Status": reimbursement.get("status"),
            "Type": reimbursement.get("type"),
            "Employee Name": employee_name,
            "Merchant": reimbursement.get("merchantName"),
            "Expense Date": reimbursement.get("occurredDate"),
            "Submitted Date": reimbursement.get("submittedTime"),
            "Amount": reimbursement.get("amount"),
            "Notes": reimbursement.get("note"),
            "Payment Method": reimbursement.get("fulfillmentType"),
            "Budget Name": budget_name,
            "Employee ID": user_id,
            "Budget ID": budget_id,
        }

        # Preserve reimbursement accounting fields
        for field in reimbursement.get("customFields") or []:

            field_name = field.get("name")

            if not field_name:
                continue

            values = field.get("selectedValues") or []

            row[f"Custom: {field_name}"] = ", ".join(
                str(value.get("value"))
                for value in values
                if value.get("value") is not None
            )

        rows.append(row)

    return pd.DataFrame(rows)
