import requests
from urllib.parse import urlencode
import streamlit as st
import json


def build_authorization_url(login_url, client_id, redirect_uri, state):

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }

    return f"{login_url}/oauth/authorize?" + urlencode(auth_params)


def exchange_authorization_code(
    login_url,
    client_id,
    client_secret,
    authorization_code,
    redirect_uri,
):
    token_url = f"{login_url}/oauth/token"

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": authorization_code,
        "redirect_uri": redirect_uri,
    }

    response = requests.post(token_url, json=payload)

    if not response.ok:
        raise RuntimeError(f"Procore connection failed.\n{response.text}")

    return response.json()


def get_companies(api_url, procore_access_token):
    headers = {
        "Authorization": f"Bearer {procore_access_token}",
    }

    companies_url = f"{api_url}/rest/v1.0/companies"

    response = requests.get(
        companies_url,
        headers=headers,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(f"Could not load Procore companies.\n{response.text}")

    return response.json()


def get_all_projects(
    api_url,
    procore_access_token,
    company_id,
):
    headers = {
        "Authorization": f"Bearer {procore_access_token}",
        "Procore-Company-Id": str(company_id),
    }

    all_projects = []

    page = 1
    per_page = 100

    while True:
        params = {
            "company_id": company_id,
            "per_page": per_page,
            "page": page,
            "filters[by_status]": "active",
        }

        response = requests.get(
            f"{api_url}/rest/v1.1/projects",
            headers=headers,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        page_projects = response.json()
        all_projects.extend(page_projects)

        total = int(response.headers.get("Total", 0))

        if len(all_projects) >= total:
            break

        page += 1

    return all_projects


def get_timecards(
    api_url,
    procore_access_token,
    company_id,
    projects,
    start_date,
    end_date,
    progress_callback=None,
):
    headers = {
        "Authorization": f"Bearer {procore_access_token}",
        "Procore-Company-Id": str(company_id),
    }

    all_timecards = []
    failed_projects = []

    for index, project in enumerate(projects):
        project_id = project["id"]
        project_name = project["name"]

        timecards_url = (
            f"{api_url}/rest/v1.0/projects/" f"{project_id}/timecard_entries"
        )

        project_timecards = []

        page = 1
        per_page = 100

        while True:
            params = {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "per_page": per_page,
                "page": page,
            }

            response = requests.get(
                timecards_url,
                headers=headers,
                params=params,
            )

            if not response.ok:
                failed_projects.append(project_name)

                st.error(
                    f"Failed to load timecards for {project_name}\n"
                    f"HTTP Status: {response.status_code}\n"
                    f"Error: {response.text[:500]}"
                )
                break

            page_timecards = response.json()

            project_timecards.extend(page_timecards)

            total = int(response.headers.get("Total", 0))

            if len(project_timecards) >= total:
                break

            page += 1

        # Procore's timecard response doesn't contain the project
        # name we need for Sage, so attach it here.
        for timecard in project_timecards:
            timecard["project_id"] = project_id
            timecard["project_name"] = project_name

            all_timecards.append(timecard)

        if progress_callback and projects:
            progress_callback((index + 1) / len(projects))

    return all_timecards, failed_projects


def procore_get(
    url,
    access_token,
    company_id=None,
    params=None,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    if company_id is not None:
        headers["Procore-Company-Id"] = str(company_id)

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError("PROCORE_SESSION_EXPIRED")

    if response.status_code == 403:
        raise RuntimeError("PROCORE_PERMISSION_DENIED")

    response.raise_for_status()

    return response.json()


def mark_timecards_completed(
    api_url,
    procore_access_token,
    company_id,
    timecards,
):
    """
    Mark the exported Procore timesheets as completed.
    """

    timecard_ids = {tc.get("id") for tc in timecards if tc.get("id")}

    if not timecard_ids:
        raise RuntimeError("No Procore timesheet IDs were found.")

    headers = {
        "Authorization": f"Bearer {procore_access_token}",
        "Procore-Company-Id": str(company_id),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{api_url}/rest/v1.0/companies/" f"{company_id}/timesheets"

    payload = {"updates": [{"id": timecard_id} for timecard_id in timecard_ids]}

    response = requests.patch(
        url,
        headers=headers,
        params={
            "approval_status": "completed",
        },
        json=payload,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            "Could not mark Procore timesheets completed.\n"
            f"Status: {response.status_code}\n"
            f"{response.text}"
        )

    return list(timecard_ids), []
