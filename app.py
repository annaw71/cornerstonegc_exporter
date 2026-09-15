import json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import requests
import secrets
import hashlib
import hmac

from datetime import date, timedelta

from procore import (
    build_authorization_url,
    get_companies,
    get_all_projects,
    get_timecards,
    procore_get,
    mark_timecards_completed,
)

from validators import validate_timecards

from sage_formatter import (
    format_timecards_for_sage,
    make_sage_clipboard_text,
    get_payroll_totals,
)

from ramp_auth import get_ramp_access_token, get_accounting_connection
from ramp import get_ready_transactions, mark_transactions_exported
from ramp_formatter import format_ramp_transactions

# SELECTOR

tool = st.sidebar.radio(
    "Choose Tool",
    [
        "Procore Payroll Exporter",
        "Ramp Transactions Exporter",
    ],
)

if tool == "Procore Payroll Exporter":

    # ============================================================
    # CONFIGURATION
    # ============================================================

    st.title("Procore Payroll Exporter")

    st.caption("Procore → Sage 100 Contractor")

    client_id = st.secrets["PROCORE_CLIENT_ID"]
    client_secret = st.secrets["PROCORE_CLIENT_SECRET"]
    login_url = st.secrets["PROCORE_LOGIN_URL"]
    api_url = st.secrets["PROCORE_API_URL"]
    redirect_uri = st.secrets["PROCORE_REDIRECT_URI"]

    def create_oauth_state():
        nonce = secrets.token_urlsafe(32)

        signature = hmac.new(
            client_secret.encode(),
            nonce.encode(),
            hashlib.sha256,
        ).hexdigest()

        return f"{nonce}.{signature}"

    def validate_oauth_state(state):
        if not state or "." not in state:
            return False

        try:
            nonce, signature = state.rsplit(".", 1)
        except ValueError:
            return False

        expected_signature = hmac.new(
            client_secret.encode(),
            nonce.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(
            signature,
            expected_signature,
        )

    st.subheader("Connect to Procore")

    code = st.query_params.get("code")
    oauth_error = st.query_params.get("error")
    returned_state = st.query_params.get("state")

    if oauth_error:
        st.error(f"Procore authorization failed: {oauth_error}")
        st.stop()

    # ------------------------------------------------------------
    # CALLBACK FROM PROCORE
    # ------------------------------------------------------------

    if code and "procore_access_token" not in st.session_state:

        if not validate_oauth_state(returned_state):
            st.error("Invalid Procore OAuth state.")
            st.stop()

        token_response = requests.post(
            f"{login_url}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )

        if not token_response.ok:
            st.error("Could not connect to Procore.")
            st.code(token_response.text)
            st.stop()

        tokens = token_response.json()

        st.session_state["procore_access_token"] = tokens["access_token"]
        st.session_state["procore_refresh_token"] = tokens.get("refresh_token")

        st.query_params.clear()
        st.rerun()

    # ------------------------------------------------------------
    # NOT CONNECTED YET
    # ------------------------------------------------------------

    elif "procore_access_token" not in st.session_state:

        oauth_state = create_oauth_state()

        authorization_url = build_authorization_url(
            login_url=login_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=oauth_state,
        )

        st.link_button(
            "Authorize Procore",
            authorization_url,
        )

    # ------------------------------------------------------------
    # CONNECTED
    # ------------------------------------------------------------

    else:
        st.success("Connected to Procore.")

        if st.button("Disconnect Procore"):
            st.session_state.pop(
                "procore_access_token",
                None,
            )

            st.session_state.pop(
                "procore_refresh_token",
                None,
            )

            st.session_state.pop(
                "company_id",
                None,
            )

            st.session_state.pop(
                "procore_oath_state",
                None,
            )

            st.rerun()

    # ============================================================
    # 2. COMPANY
    # ============================================================

    if "procore_access_token" in st.session_state:

        companies = get_companies(
            api_url=api_url,
            procore_access_token=st.session_state["procore_access_token"],
        )

        target_company_id = int(st.secrets["PROCORE_COMPANY_ID"])

        company = next(
            (company for company in companies if company["id"] == target_company_id),
            None,
        )

        if company is None:
            st.error(
                "Your Procore account does not have access to the configured company."
            )
            st.stop()

        st.session_state["company_id"] = company["id"]

        st.write(f"Company: {company['name']}")

    # ============================================================
    # 3. PAY PERIOD
    # ============================================================

    st.divider()

    st.subheader("Select Pay Period")

    def get_previous_week():

        today = date.today()

        # Monday of the current week
        current_monday = today - timedelta(days=today.weekday())

        # Previous completed week
        previous_monday = current_monday - timedelta(days=7)
        previous_sunday = previous_monday + timedelta(days=6)
        check_date = current_monday + timedelta(days=11)

        return previous_monday, previous_sunday, check_date

    def change_pay_period(days):
        st.session_state["pay_period_start"] += timedelta(days=days)
        st.session_state["pay_period_end"] += timedelta(days=days)
        st.session_state["check_date"] += timedelta(days=days)
        st.session_state.pop("timecards", None)
        st.session_state.pop("payroll_df", None)

    # Set the default pay period only once.
    if "pay_period_start" not in st.session_state:
        previous_monday, previous_sunday, check_date = get_previous_week()
        st.session_state["pay_period_start"] = previous_monday
        st.session_state["pay_period_end"] = previous_sunday
        st.session_state["check_date"] = check_date

    # Navigation buttons
    st.markdown(
        """
        <style>
        div.stButton > button {
            font-size: 1.25rem !important;
            font-weight: 400 !important;
            padding: 10px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    back_col, date_col, forward_col = st.columns([1, 5, 1])

    with back_col:
        if st.button(
            "<",
            help="Previous week",
            use_container_width=True,
        ):
            change_pay_period(-7)
            st.rerun()

    with date_col:
        start_date = st.session_state["pay_period_start"]
        end_date = st.session_state["pay_period_end"]
        check_date = st.session_state["check_date"]

        st.markdown(
            f"""
            <div style="
                text-align: center;
                font-size: 1.25rem;
                font-weight: 400;
                padding-top: 0.1rem;
            ">
                Pay Period: {start_date.strftime("%m/%d/%Y")} - {end_date.strftime("%m/%d/%Y")}<br>  
                (Check Date: {check_date.strftime("%m/%d/%Y")})
            </div>
            """,
            unsafe_allow_html=True,
        )

    with forward_col:
        if st.button(
            ">",
            help="Next week",
            use_container_width=True,
        ):
            change_pay_period(7)
            st.rerun()

    # ============================================================
    # LOAD PROJECTS
    # ============================================================

    if (
        "procore_access_token" in st.session_state
        and "company_id" in st.session_state
        and "projects" not in st.session_state
    ):
        try:
            projects = get_all_projects(
                api_url=api_url,
                procore_access_token=st.session_state["procore_access_token"],
                company_id=st.session_state["company_id"],
            )
            st.session_state["projects"] = projects

        except RuntimeError as exc:

            if str(exc) == "PROCORE_SESSION_EXPIRED":
                st.warning(
                    "Your Procore session expired." "Please authorize Procore again."
                )
                st.session_state.pop(
                    "procore_access_token",
                    None,
                )
                st.stop()
            elif str(exc) == " PROCORE_PERMISSION_DENIED":
                st.error(
                    "Your Procore account does not have "
                    "permission to access this data."
                )
                st.stop()
            else:
                raise

        except Exception as exc:
            st.error("Could not load Procore projects.")
            st.code(str(exc))

    # ============================================================
    # 4. PULL TIMESHEETS
    # ============================================================

    if (
        "procore_access_token" in st.session_state
        and "company_id" in st.session_state
        and "projects" in st.session_state
    ):
        st.divider()

        st.subheader("Pull Procore Timesheets")

        if st.button("Pull Timesheets"):
            progress_bar = st.progress(0)

            try:
                (
                    all_timecards,
                    failed_projects,
                ) = get_timecards(
                    api_url=api_url,
                    procore_access_token=st.session_state["procore_access_token"],
                    company_id=st.session_state["company_id"],
                    projects=st.session_state["projects"],
                    start_date=start_date,
                    end_date=end_date,
                    progress_callback=(progress_bar.progress),
                )

                st.session_state["timecards"] = all_timecards

                for project_name in failed_projects:
                    st.warning("Could not load timecards " f"for {project_name}.")

            except Exception as exc:
                st.error("Could not load timesheets.")
                st.code(str(exc))

    # ============================================================
    # VALIDATE TIMECARDS
    # ============================================================

    if "timecards" in st.session_state:
        all_timecards = st.session_state["timecards"]

        validation = validate_timecards(all_timecards)

        # --------------------------------------------------------
        # BLOCK EXPORT IF ERRORS EXIST
        # --------------------------------------------------------

        if validation["status"] == "completed":
            st.info("Timecards are already completed " "for this pay period.")
            st.stop()

        if validation["errors"]:
            st.write(
                f"**{len(validation['errors'])} error"
                f"{'s' if len(validation['errors']) != 1 else ''} "
                f"found.**"
            )

            error_df = pd.DataFrame(
                {
                    "Problem": validation["errors"],
                }
            )

            st.dataframe(
                error_df,
                use_container_width=True,
                hide_index=True,
            )

            if st.button("Refresh From Procore"):
                st.session_state.pop("timecards", None)
                st.session_state.pop("payroll_df", None)
                st.rerun()

            st.stop()

        else:
            # ----------------------------------------------------
            # FORMAT FOR SAGE
            # ----------------------------------------------------

            payroll_df = format_timecards_for_sage(all_timecards)

            sage_copy_text = make_sage_clipboard_text(payroll_df)

            totals = get_payroll_totals(payroll_df)

            st.session_state["payroll_df"] = payroll_df

            # ----------------------------------------------------
            # DISPLAY TOTALS
            # ----------------------------------------------------

            st.success(f"{totals['row_count']} " "approved timecards ready.")

            col1, col2, col3, col4 = st.columns(4)

            col1.metric(
                "Employees",
                totals["employee_count"],
            )

            col2.metric(
                "Jobs",
                totals["job_count"],
            )

            col3.metric(
                "Total Hours",
                f"{totals['total_hours']:,.1f}",
            )

            col4.metric(
                "Overtime Hours",
                f"{totals['overtime_hours']:,.1f}",
            )

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Sick Hours",
                f"{totals['sick_hours']:,.1f}",
            )

            col2.metric(
                "Vacation Hours",
                f"{totals['vacation_hours']:,.1f}",
            )

            col3.metric(
                "Holiday Hours",
                f"{totals['holiday_hours']:,.1f}",
            )

            # ----------------------------------------------------
            # COPY TO SAGE
            # ----------------------------------------------------

            sage_copy_json = json.dumps(sage_copy_text).replace("</", "<\\/")

            row_count = len(payroll_df)

            st.html(
                f"""
                <button
                    id="copy-sage-button"
                    style="
                        padding: 0.5rem 0.9rem;
                        font-size: 1rem;
                        cursor: pointer;
                        border-radius: 0.5rem;
                        border: 1px solid #ccc;
                    "
                >
                    Copy Table for Sage
                </button>

                <span
                    id="copy-sage-status"
                    style="margin-left: 10px;"
                ></span>

                <script>
                    const button =
                        document.getElementById("copy-sage-button");

                    const status =
                        document.getElementById("copy-sage-status");

                    const text = {sage_copy_json};

                    button.addEventListener("click", async () => {{
                        try {{
                            if (
                                navigator.clipboard &&
                                window.isSecureContext
                            ) {{
                                await navigator.clipboard.writeText(text);
                            }} else {{
                                const textarea =
                                    document.createElement("textarea");

                                textarea.value = text;
                                textarea.style.position = "fixed";
                                textarea.style.left = "-9999px";

                                document.body.appendChild(textarea);

                                textarea.focus();
                                textarea.select();

                                const copied =
                                    document.execCommand("copy");

                                document.body.removeChild(textarea);

                                if (!copied) {{
                                    throw new Error(
                                        "Fallback copy failed."
                                    );
                                }}
                            }}

                            button.innerText =
                                "✅ Copied {row_count} rows";

                            status.innerText = "";

                        }} catch (error) {{
                            console.error(error);

                            status.innerText =
                                "❌ Clipboard blocked by browser.";
                        }}
                    }});
                </script>
                """,
                unsafe_allow_javascript=True,
            )

            st.markdown("""
                ### Enter Timecards into Sage

                1. Go to **Sage 5-6-2**
                2. Paste the copied table into the **first cell**
                3. Verify that everything looks correct
                4. Click **Save**
                """)

            # -----------------------------------------------------
            # MARK EXPORTED TIME COMPLETED
            # ----------------------------------------------------

            st.divider()

            if st.button(
                "Mark Exported Time as Completed",
                type="primary",
            ):
                try:
                    completed, failed = mark_timecards_completed(
                        api_url=api_url,
                        procore_access_token=st.session_state["procore_access_token"],
                        company_id=st.session_state["company_id"],
                        timecards=all_timecards,
                    )

                    if failed:
                        st.error(
                            f"{len(failed)} timesheet(s) could not "
                            "be marked completed."
                        )

                        for failure in failed:
                            st.code(str(failure))

                    else:
                        st.success(
                            f"{len(completed)} timesheet(s) "
                            "marked as completed in Procore."
                        )

                        st.session_state.pop("timecards", None)
                        st.session_state.pop("payroll_df", None)

                        st.rerun()

                except Exception as exc:
                    st.error("Could not mark the exported time as completed.")
                    st.code(str(exc))

            # ----------------------------------------------------
            # CSV DOWNLOAD
            # ----------------------------------------------------

            csv = payroll_df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label=("Optional: Download " "Payroll Preview CSV"),
                data=csv,
                file_name=(f"procore_payroll_" f"{start_date}_to_" f"{end_date}.csv"),
                mime="text/csv",
            )

elif tool == "Ramp Transactions Exporter":

    st.title("Ramp Transactions Exporter")

    st.write("Export card transactions currently marked Ready to Export in Ramp.")

    if st.button("TEST Ramp Sync Endpoint"):

        try:

            access_token = get_ramp_access_token(
                st.secrets["RAMP_CLIENT_ID"],
                st.secrets["RAMP_CLIENT_SECRET"],
            )

            fake_transaction = [{"id": "11111111-1111-1111-1111-111111111111"}]

            result = mark_transactions_exported(
                access_token,
                fake_transaction,
            )

            st.success("Ramp accepted the request.")
            st.json(result)

        except Exception as e:

            st.error("Ramp rejected the request.")
            st.code(str(e))

    # ============================================================
    # LOAD READY TRANSACTIONS
    # ============================================================

    if st.button("Load Ready Transactions"):

        try:

            access_token = get_ramp_access_token(
                st.secrets["RAMP_CLIENT_ID"],
                st.secrets["RAMP_CLIENT_SECRET"],
            )

            transactions = get_ready_transactions(access_token)

            # Store these so they survive Streamlit reruns
            st.session_state["ramp_access_token"] = access_token
            st.session_state["ramp_transactions"] = transactions

            if transactions:
                st.session_state["ramp_df"] = format_ramp_transactions(transactions)
            else:
                st.session_state.pop("ramp_df", None)

            st.rerun()

        except Exception as e:

            st.error(f"Could not load Ramp transactions: {e}")

    # ============================================================
    # DISPLAY LOADED TRANSACTIONS
    # ============================================================

    if "ramp_transactions" in st.session_state:

        transactions = st.session_state["ramp_transactions"]

        if not transactions:

            st.success("There are currently no card transactions to export.")

        else:

            df = st.session_state["ramp_df"]

            st.success(f"{len(transactions)} Ramp transactions ready to export.")

            st.dataframe(
                df,
                use_container_width=True,
            )

            # ====================================================
            # COPY TO SAGE
            # ====================================================

            sage_clipboard_text = df.to_csv(
                sep="\t",
                index=False,
                header=False,
            )

            sage_copy_json = json.dumps(sage_clipboard_text)
            row_count = len(df)

            st.html(
                f"""
                <button
                    id="copy-ramp-sage-button"
                    style="
                        padding: 0.5rem 0.9rem;
                        font-size: 1rem;
                        cursor: pointer;
                        border-radius: 0.5rem;
                        border: 1px solid #ccc;
                    "
                >
                    Copy Table for Sage
                </button>

                <span
                    id="copy-ramp-sage-status"
                    style="margin-left: 10px;"
                ></span>

                <script>
                    const button =
                        document.getElementById(
                            "copy-ramp-sage-button"
                        );

                    const status =
                        document.getElementById(
                            "copy-ramp-sage-status"
                        );

                    const text = {sage_copy_json};

                    button.addEventListener(
                        "click",
                        async () => {{

                            try {{

                                if (
                                    navigator.clipboard &&
                                    window.isSecureContext
                                ) {{

                                    await navigator.clipboard.writeText(
                                        text
                                    );

                                }} else {{

                                    const textarea =
                                        document.createElement(
                                            "textarea"
                                        );

                                    textarea.value = text;

                                    textarea.style.position = "fixed";
                                    textarea.style.left = "-9999px";

                                    document.body.appendChild(
                                        textarea
                                    );

                                    textarea.focus();
                                    textarea.select();

                                    const copied =
                                        document.execCommand("copy");

                                    document.body.removeChild(
                                        textarea
                                    );

                                    if (!copied) {{
                                        throw new Error(
                                            "Fallback copy failed."
                                        );
                                    }}
                                }}

                                button.innerText =
                                    "✅ Copied {row_count} rows";

                                status.innerText = "";

                            }} catch (error) {{

                                console.error(error);

                                status.innerText =
                                    "❌ Clipboard blocked by browser.";
                            }}
                        }}
                    );
                </script>
                """,
                unsafe_allow_javascript=True,
            )

            # ====================================================
            # SAGE INSTRUCTIONS
            # ====================================================

            st.markdown("""
                ### Enter Transactions into Sage

                1. Click **Copy Table for Sage**
                2. Paste the transactions into Sage
                3. Verify the transactions
                4. Save them in Sage
                5. Once saved, mark the transactions as exported in Ramp
                """)

            # ====================================================
            # MARK TRANSACTIONS EXPORTED IN RAMP
            # ====================================================

            st.divider()

            st.subheader("Finish Export")

            st.warning(
                "Only mark these transactions as exported AFTER "
                "you have pasted them into Sage and successfully saved them."
            )

            confirm_export = st.checkbox(
                "I have successfully entered and saved these transactions in Sage."
            )

            if st.button(
                "Mark Transactions Exported in Ramp",
                type="primary",
                disabled=not confirm_export,
            ):

                try:

                    # Get a fresh token in case the original token expired
                    access_token = get_ramp_access_token(
                        st.secrets["RAMP_CLIENT_ID"],
                        st.secrets["RAMP_CLIENT_SECRET"],
                    )

                    result = mark_transactions_exported(
                        access_token,
                        transactions,
                    )

                    st.success(
                        f"{len(transactions)} transaction"
                        f"{'s' if len(transactions) != 1 else ''} "
                        "marked as exported in Ramp."
                    )

                    # Clear the old transactions
                    st.session_state.pop("ramp_transactions", None)
                    st.session_state.pop("ramp_df", None)
                    st.session_state.pop("ramp_access_token", None)

                    st.rerun()

                except Exception as e:

                    st.error(
                        "The transactions were entered into Sage, "
                        "but Ramp could not mark them as exported."
                    )

                    st.code(str(e))

            # ====================================================
            # CSV DOWNLOAD
            # ====================================================

            st.divider()

            csv = df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label="Optional CSV Download",
                data=csv,
                file_name="ramp_ready_transactions.csv",
                mime="text/csv",
            )
