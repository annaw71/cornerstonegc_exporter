import pandas as pd


def get_accounting_field(line_item, field_name):
    selections = line_item.get("accounting_field_selections", [])

    for selection in selections:
        category_info = selection.get("category_info") or {}

        if category_info.get("name") == field_name:
            return {
                "name": selection.get("name"),
                "code": selection.get("external_code"),
            }

    return {
        "name": None,
        "code": None,
    }


def format_ramp_transactions(transactions):

    rows = []

    for transaction in transactions:

        card_holder = transaction.get("card_holder") or {}

        employee_name = " ".join(
            filter(
                None,
                [
                    card_holder.get("first_name"),
                    card_holder.get("last_name"),
                ],
            )
        )

        line_items = transaction.get("line_items") or []

        if not line_items:
            line_items = [{}]

        for line_item in line_items:

            gl_account = get_accounting_field(
                line_item,
                "Sage GL Account",
            )

            # get transaction #
            transaction_num = transaction.get("accounting_date")

            if transaction_num:
                transaction_num = pd.to_datetime(transaction_num).strftime(
                    "%Y-%m-%d" + "T" + "%H:%M:%S"
                )

            # get transaction amount
            if transaction.get("amount") > 0:
                charge = transaction.get("amount")
                credit = None

            else:
                credit = transaction.get("amount")
                charge = None

            # get posted date, format
            posted_date = transaction.get("user_transaction_time")

            if posted_date:
                posted_date = pd.to_datetime(posted_date).strftime("%m/%d/%Y")

            account = None

            if gl_account["code"] and gl_account["name"]:
                account = f'{gl_account["code"]} - {gl_account["name"]}'
            elif gl_account["code"]:
                account = gl_account["code"]
            elif gl_account["name"]:
                account = gl_account["name"]

            rows.append(
                {
                    "Credit Card": "1 - Ramp",
                    "Include": "Include",
                    "Transaction #": transaction_num,
                    "Description": employee_name,
                    "Payee": transaction.get("merchant_name"),
                    "Charge Amount": charge,
                    "Credit Amount": credit,
                    "Posted Date": posted_date,
                    "Notes": transaction.get("memo"),
                    "Account": account,
                    "Subaccount": None,
                    "Job": None,
                    "Phase": None,
                    "Job Cost Code": None,
                    "Job Cost Type": None,
                    "Equipment": None,
                    "Equipment Cost Code": None,
                    "Equipment Cost Type": None,
                }
            )

    return pd.DataFrame(rows)
