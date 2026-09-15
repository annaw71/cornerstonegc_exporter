import requests

RAMP_BASE_URL = "https://api.ramp.com/developer/v1"


def get_ready_transactions(access_token):

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    transactions = []

    params = {
        "sync_status": "SYNC_READY",
        "page_size": 100,
    }

    url = f"{RAMP_BASE_URL}/transactions"

    while True:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        # response.raise_for_status()

        if not response.ok:
            raise Exception(f"Ramp API error {response.status_code}: {response.text}")

        result = response.json()

        transactions.extend(result.get("data", []))

        page = result.get("page", {})
        next_page = page.get("next")

        if not next_page:
            break

        params["start"] = next_page

    return transactions


import uuid


def mark_transactions_exported(access_token, transactions):

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    successful_syncs = []

    for transaction in transactions:

        transaction_id = transaction.get("id")

        if not transaction_id:
            continue

        successful_syncs.append(
            {
                "id": transaction_id,
                "reference_id": f"SAGE-{transaction_id}",
            }
        )

    if not successful_syncs:
        raise Exception("No valid Ramp transaction IDs were found.")

    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "sync_type": "TRANSACTION_SYNC",
        "successful_syncs": successful_syncs,
    }

    response = requests.post(
        f"{RAMP_BASE_URL}/accounting/syncs",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if not response.ok:
        raise Exception(f"Ramp sync error {response.status_code}: {response.text}")

    return response.json()
