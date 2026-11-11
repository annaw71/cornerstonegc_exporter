import requests
import msal
import streamlit as st
import csv
from io import StringIO
from urllib.parse import quote
from datetime import datetime, timezone


def get_graph_token():

    authority = f"https://login.microsoftonline.com/" f"{st.secrets['MS_TENANT_ID']}"

    app = msal.ConfidentialClientApplication(
        st.secrets["MS_CLIENT_ID"],
        authority=authority,
        client_credential=st.secrets["MS_CLIENT_SECRET"],
    )

    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )

    if "access_token" not in result:
        raise Exception(
            f"Could not get Microsoft Graph token: "
            f"{result.get('error_description')}"
        )

    return result["access_token"]


def get_sharepoint_site_id():

    token = get_graph_token()

    hostname = st.secrets["SHAREPOINT_HOSTNAME"]
    site_path = st.secrets["SHAREPOINT_SITE_PATH"]

    url = f"https://graph.microsoft.com/v1.0/" f"sites/{hostname}:{site_path}"

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    response.raise_for_status()

    return response.json()["id"]


def upload_csv_to_sharepoint(
    csv_data,
    filename,
    folder_path,
):

    token = get_graph_token()
    site_id = get_sharepoint_site_id()

    full_path = f"{folder_path}/{filename}"

    encoded_path = quote(full_path, safe="/")

    url = (
        f"https://graph.microsoft.com/v1.0/"
        f"sites/{site_id}/drive/root:"
        f"/{encoded_path}:/content"
    )

    if isinstance(csv_data, str):
        csv_data = csv_data.encode("utf-8")

    response = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/csv",
        },
        data=csv_data,
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


def get_bse_reimbursement_sync_history():
    """
    Read confirmed reimbursement IDs from SharePoint.

    Returns a set of previously synced reimbursement IDs.
    Raises an error if the history cannot be read.
    """

    token = get_graph_token()
    site_id = get_sharepoint_site_id()

    file_path = "Reports/Streamlit/" "BSE_Reimbursement_Sync_History.csv"

    encoded_path = quote(file_path, safe="/")

    url = (
        f"https://graph.microsoft.com/v1.0/"
        f"sites/{site_id}/drive/root:"
        f"/{encoded_path}:/content"
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
        },
        timeout=60,
    )

    response.raise_for_status()

    csv_text = response.content.decode("utf-8-sig")

    reader = csv.DictReader(StringIO(csv_text))

    required_columns = {
        "Reimbursement ID",
        "Sync Status",
    }

    if not required_columns.issubset(set(reader.fieldnames or [])):
        raise ValueError("SharePoint sync history is missing " "required columns.")

    confirmed_ids = set()
    seen_ids = set()
    row_count = 0

    for row in reader:
        row_count += 1

        reimbursement_id = (row.get("Reimbursement ID") or "").strip()

        sync_status = (row.get("Sync Status") or "").strip().casefold()

        if not reimbursement_id:
            raise ValueError(f"Missing reimbursement ID on row " f"{row_count + 1}.")

        if reimbursement_id in seen_ids:
            raise ValueError(
                "Duplicate reimbursement ID in "
                f"SharePoint history: {reimbursement_id}"
            )

        seen_ids.add(reimbursement_id)

        if sync_status == "confirmed":
            confirmed_ids.add(reimbursement_id)

    if row_count == 0:
        raise ValueError("SharePoint reimbursement history is empty.")

    if not confirmed_ids:
        raise ValueError("No confirmed reimbursement IDs found.")

    return confirmed_ids


def confirm_bse_reimbursement_sync(reimbursement_df):
    """
    Append successfully saved Sage reimbursements
    to the SharePoint sync history.

    Never replaces existing records with only new ones.
    """

    if reimbursement_df.empty:
        raise ValueError("No reimbursements to confirm.")

    token = get_graph_token()
    site_id = get_sharepoint_site_id()

    path = "Reports/Streamlit/" "BSE_Reimbursement_Sync_History.csv"

    encoded_path = quote(path, safe="/")

    base_url = f"https://graph.microsoft.com/v1.0/" f"sites/{site_id}/drive"

    headers = {
        "Authorization": f"Bearer {token}",
    }

    # Read file metadata and its current version
    metadata_url = f"{base_url}/root:/{encoded_path}"

    metadata_response = requests.get(
        metadata_url,
        headers=headers,
        timeout=60,
    )
    metadata_response.raise_for_status()

    metadata = metadata_response.json()

    item_id = quote(metadata["id"], safe="")
    original_etag = metadata.get("eTag")

    if not original_etag:
        raise RuntimeError("SharePoint did not return a file version.")

    content_url = f"{base_url}/items/{item_id}/content"

    # Download the entire existing registry
    response = requests.get(
        content_url,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    reader = csv.DictReader(StringIO(response.content.decode("utf-8-sig")))

    fieldnames = reader.fieldnames or []
    existing_rows = list(reader)

    required = {
        "Reimbursement ID",
        "Employee Name",
        "Amount",
        "Status",
        "Submitted Date",
        "Sync Status",
        "Sync Source",
        "Recorded Date",
    }

    if not required.issubset(set(fieldnames)):
        raise ValueError("SharePoint history has missing columns.")

    # Preserve the original historical baseline
    if len(existing_rows) < 3726:
        raise RuntimeError("Sync history has fewer than 3,726 rows. " "Update blocked.")

    existing_ids = set()

    for row in existing_rows:
        rid = str(row["Reimbursement ID"]).strip()

        if not rid or rid in existing_ids:
            raise RuntimeError("Invalid or duplicate ID in history.")

        existing_ids.add(rid)

    # Prepare the newly confirmed reimbursements
    new_rows = []
    new_ids = set()

    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for _, reimbursement in reimbursement_df.iterrows():

        rid = str(reimbursement.get("Reimbursement ID") or "").strip()

        if rid.lower() in ("", "nan", "none", "<na>"):
            raise ValueError("Missing reimbursement ID.")

        if rid in existing_ids or rid in new_ids:
            raise RuntimeError(
                f"Reimbursement {rid} is already " "in the sync history or this batch."
            )

        new_ids.add(rid)

        row = {column: "" for column in fieldnames}

        row.update(
            {
                "Reimbursement ID": rid,
                "Employee Name": reimbursement.get("Employee Name", ""),
                "Amount": reimbursement.get("Amount", ""),
                "Status": reimbursement.get("Status", ""),
                "Submitted Date": reimbursement.get("Submitted Date", ""),
                "Sync Status": "Confirmed",
                "Sync Source": "Streamlit",
                "Recorded Date": recorded_at,
            }
        )

        new_rows.append(row)

    # Confirm SharePoint hasn't changed while reading
    latest_response = requests.get(
        f"{base_url}/items/{item_id}",
        headers=headers,
        timeout=60,
    )
    latest_response.raise_for_status()

    if latest_response.json().get("eTag") != original_etag:
        raise RuntimeError(
            "SharePoint history changed during this "
            "operation. Refresh before confirming."
        )

    # Rebuild CSV with original rows plus new rows
    output = StringIO()

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
    )

    writer.writeheader()
    writer.writerows(existing_rows)
    writer.writerows(new_rows)

    csv_bytes = output.getvalue().encode("utf-8-sig")

    # Conditional update: reject an outdated file version
    upload_response = requests.put(
        content_url,
        headers={
            **headers,
            "Content-Type": "text/csv",
            "If-Match": original_etag,
        },
        data=csv_bytes,
        timeout=60,
    )

    if upload_response.status_code == 412:
        raise RuntimeError(
            "SharePoint history was updated by someone "
            "else. Refresh before retrying."
        )

    upload_response.raise_for_status()

    # Verify the confirmed IDs are now in SharePoint
    verified_ids = get_bse_reimbursement_sync_history()

    if not new_ids.issubset(verified_ids):
        raise RuntimeError(
            "Update could not be verified. Check " "SharePoint before trying again."
        )

    return len(new_rows)
