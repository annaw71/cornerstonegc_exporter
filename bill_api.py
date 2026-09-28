import time
import requests
import streamlit as st

BASE_URL = "https://gateway.prod.bill.com/connect"

TRANSACTIONS_PATH = "/v3/spend/transactions"

TRANSACTION_FILTER = "type:ne:DECLINE," "syncStatus:eq:NOT_SYNCED," "complete:eq:true"

PAGE_SIZE = 100
MAX_PAGES = 200


def get_headers():
    return {
        "apiToken": st.secrets["BILL_API_TOKEN"],
        "Accept": "application/json",
    }


def get_budgets():
    url = f"{BASE_URL}/v3/spend/budgets"

    response = requests.get(
        url,
        headers=get_headers(),
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def request_with_retries(url, params=None, max_attempts=5):
    """
    Make a BILL GET request and retry if BILL returns a 429 rate-limit response.
    """

    for attempt in range(1, max_attempts + 1):

        response = requests.get(
            url,
            headers=get_headers(),
            params=params,
            timeout=120,
        )

        if response.ok:
            return response

        if response.status_code == 429 and attempt < max_attempts:

            retry_after = response.headers.get("Retry-After")

            try:
                wait_seconds = int(retry_after)
            except (TypeError, ValueError):
                wait_seconds = 60

            wait_seconds = max(wait_seconds, 1)

            time.sleep(wait_seconds)
            continue

        response.raise_for_status()

    raise RuntimeError("BILL request failed after multiple attempts.")


def extract_items(data):
    """
    BILL responses may place records under different keys.
    Return the first list we find.
    """

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in [
        "results",
        "transactions",
        "items",
        "data",
        "records",
    ]:
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def get_next_page_token(data):
    if not isinstance(data, dict):
        return None

    return data.get("nextPage") or data.get("nextPageToken")


def has_accounting_integration_transactions(transaction):
    """
    Return True if BILL indicates this transaction has already
    been associated with accounting integration data.
    """

    value = transaction.get("accountingIntegrationTransactions")

    if value is None:
        return False

    if isinstance(value, list):
        return len(value) > 0

    if isinstance(value, dict):
        return len(value) > 0

    if isinstance(value, str):
        return bool(value.strip())

    return bool(value)


def has_approved_admin_reviewer(transaction):
    reviewers = transaction.get("reviewers") or []

    for reviewer in reviewers:

        if not isinstance(reviewer, dict):
            continue

        approver_type = str(reviewer.get("approverType") or "").strip().upper()

        status = str(reviewer.get("status") or "").strip().upper()

        if approver_type == "ADMIN" and status == "APPROVED":
            return True

    return False


def is_declined(transaction):
    transaction_type = str(transaction.get("transactionType") or "").strip().upper()

    return transaction_type == "DECLINE"


def flatten_json(value, prefix="", result=None):
    """
    Flatten nested BILL JSON so fields such as custom fields
    can later be used by the Sage formatter.
    """

    if result is None:
        result = {}

    if isinstance(value, dict):

        for key, child in value.items():

            next_prefix = f"{prefix}.{key}" if prefix else key

            flatten_json(
                child,
                next_prefix,
                result,
            )

    elif isinstance(value, list):

        values = []

        for item in value:

            if isinstance(item, dict):

                display_value = item.get("value") or item.get("name")

                if display_value is not None:
                    values.append(str(display_value))
                else:
                    values.append(str(item))

            else:
                values.append(str(item))

        result[prefix] = "; ".join(values)

    elif value is None:
        result[prefix] = ""

    else:
        result[prefix] = value

    return result


def add_tag_columns(tags, row):
    """
    Convert BILL tag selections into named columns.
    """

    if not isinstance(tags, list):
        return

    for tag in tags:

        if not isinstance(tag, dict):
            continue

        tag_type = tag.get("tagType") or {}

        tag_name = tag_type.get("name") if isinstance(tag_type, dict) else None

        tag_name = tag_name or tag.get("name")

        if not tag_name:
            continue

        selected_values = []

        for value in tag.get("selectedTagValues") or []:

            if isinstance(value, dict):
                text = value.get("value") or value.get("name")
            else:
                text = value

            if text is not None and str(text).strip():
                selected_values.append(str(text).strip())

        row[tag_name] = "; ".join(selected_values)


def add_custom_field_columns(custom_fields, row):
    """
    Convert BILL custom fields into normal dictionary columns.
    """

    if not isinstance(custom_fields, list):
        return

    for field in custom_fields:

        if not isinstance(field, dict):
            continue

        field_name = field.get("name")

        if not field_name:
            continue

        values = []

        selected_values = field.get("selectedValues")

        if isinstance(selected_values, list):

            for value in selected_values:

                if isinstance(value, dict):
                    text = value.get("value") or value.get("name")
                else:
                    text = value

                if text is not None and str(text).strip():
                    values.append(str(text).strip())

        else:

            single_value = field.get("value")

            if not single_value:
                selected_value = field.get("selectedValue")

                if isinstance(selected_value, dict):
                    single_value = selected_value.get("value") or selected_value.get(
                        "name"
                    )

            if single_value:
                values.append(str(single_value).strip())

        row[field_name] = "; ".join(values)


def build_transaction_row(transaction):
    """
    Flatten a BILL transaction while giving tags and
    custom fields useful column names.
    """

    row = {}

    for key, value in transaction.items():

        if key == "tags":
            add_tag_columns(value, row)

        elif key == "customFields":
            add_custom_field_columns(value, row)

        else:
            flatten_json(
                value,
                key,
                row,
            )

    return row


def split_multi_value(value):
    if not value:
        return []

    return [item.strip() for item in str(value).split(";") if item.strip()]


def merge_general_ledger_accounts(row):
    """
    Combine the three possible BILL Sage GL fields.

    Returns:
        conflict: bool
        message: str | None
    """

    source_fields = [
        "Sage General Ledger Account",
        "1 - Sage Direct Expense GL Accounts",
        "Sage Vehicle and Equipment GL Accounts",
    ]

    distinct_values = set()
    populated_sources = []

    for field in source_fields:

        values = split_multi_value(row.get(field))

        if not values:
            continue

        populated_sources.append(f"{field} = {' | '.join(values)}")

        for value in values:
            distinct_values.add(value)

    if len(distinct_values) == 1:

        row["Sage General Ledger Account"] = next(iter(distinct_values))

        conflict = False
        message = None

    elif len(distinct_values) > 1:

        row["Sage General Ledger Account"] = ""

        conflict = True

        message = "Multiple Sage GL account values found: " + "; ".join(
            populated_sources
        )

    else:

        row.pop(
            "Sage General Ledger Account",
            None,
        )

        conflict = False
        message = None

    row.pop(
        "1 - Sage Direct Expense GL Accounts",
        None,
    )

    row.pop(
        "Sage Vehicle and Equipment GL Accounts",
        None,
    )

    return conflict, message


def get_transactions():
    """
    Pull and filter BILL Spend & Expense transactions.

    Returns a dictionary containing:
        transactions
        exported_ids
        sync_eligible_ids
        sync_excluded_ids
        warnings
        skipped_counts
    """

    url = f"{BASE_URL}{TRANSACTIONS_PATH}"

    transactions = []

    seen_ids = set()

    exported_ids = []
    sync_eligible_ids = []
    sync_excluded_ids = []

    warnings = []

    skipped_counts = {
        "already_integrated": 0,
        "not_admin_approved": 0,
        "declined": 0,
        "duplicates": 0,
    }

    next_page = None
    seen_pages = set()

    for page_number in range(
        1,
        MAX_PAGES + 1,
    ):

        if next_page:

            params = {
                "nextPage": next_page,
                "max": PAGE_SIZE,
            }

        else:

            params = {
                "filters": TRANSACTION_FILTER,
                "max": PAGE_SIZE,
            }

        response = request_with_retries(
            url,
            params=params,
        )

        data = response.json()

        page_transactions = extract_items(data)

        for transaction in page_transactions:

            if has_accounting_integration_transactions(transaction):
                skipped_counts["already_integrated"] += 1
                continue

            if not has_approved_admin_reviewer(transaction):
                skipped_counts["not_admin_approved"] += 1
                continue

            if is_declined(transaction):
                skipped_counts["declined"] += 1
                continue

            transaction_id = transaction.get("id")

            if transaction_id:

                transaction_id = str(transaction_id)

                if transaction_id in seen_ids:
                    skipped_counts["duplicates"] += 1
                    continue

                seen_ids.add(transaction_id)

            row = build_transaction_row(transaction)

            conflict, message = merge_general_ledger_accounts(row)

            if transaction_id:

                exported_ids.append(transaction_id)

                if conflict:
                    sync_excluded_ids.append(transaction_id)
                else:
                    sync_eligible_ids.append(transaction_id)

            if message:
                warnings.append(
                    {
                        "transaction_id": transaction_id,
                        "message": message,
                    }
                )

            transactions.append(row)

        next_page = get_next_page_token(data)

        if not next_page:
            break

        if next_page in seen_pages:
            warnings.append(
                {
                    "transaction_id": None,
                    "message": (
                        "BILL returned the same "
                        "pagination token twice. "
                        "Pagination was stopped."
                    ),
                }
            )
            break

        seen_pages.add(next_page)

    else:
        raise RuntimeError(
            f"BILL transaction pull stopped after " f"{MAX_PAGES} pages."
        )

    return {
        "transactions": transactions,
        "exported_ids": exported_ids,
        "sync_eligible_ids": sync_eligible_ids,
        "sync_excluded_ids": sync_excluded_ids,
        "warnings": warnings,
        "skipped_counts": skipped_counts,
    }
