import requests
import msal
import streamlit as st
import csv
from io import StringIO
from urllib.parse import quote


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
