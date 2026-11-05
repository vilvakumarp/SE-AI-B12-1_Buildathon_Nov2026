"""Customer support desk and admin knowledge-base console."""
import asyncio, csv, hashlib, html, json, os, re, sqlite3, uuid
import tempfile
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv
from agents.log_agent import FIELDS, LOG_PATH, update_ticket
from observability import api_status
from rag_kb import INDEX_PATH, build_index
from support_history import link_attachment, list_attachments, list_conversations, list_messages, save_attachment, save_feedback, save_message
from team import register_confirmed_complaint, run_support_team
from autogen_team import summarize_complaint_history
from time_utils import now_ist
from user_store import add_user, authenticate, list_users, reset_password, set_enabled

def recover_missing_ticket_rows(path):
    """Restore ticket rows lost from CSV when their chat history still exists."""
    db_path = path.parent / "support_history.sqlite3"
    if not db_path.exists(): return
    try:
        with sqlite3.connect(db_path) as conn:
            messages = conn.execute("SELECT customer,ticket_id,role,content,created_at FROM messages WHERE ticket_id IS NOT NULL AND ticket_id != '' ORDER BY id").fetchall()
        existing = set()
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as stream:
                existing = {row.get("ticket_id") for row in csv.DictReader(stream) if row.get("ticket_id")}
        grouped = {}
        for customer_name, ticket_id, role, content, created_at in messages:
            grouped.setdefault(ticket_id, {"customer_name": customer_name, "created_at": created_at, "complaint": "", "response": ""})
            if role == "customer" and not grouped[ticket_id]["complaint"]: grouped[ticket_id]["complaint"] = repair_text(content)
            elif role == "agent": grouped[ticket_id]["response"] = repair_text(content)
        missing = [(ticket_id, row) for ticket_id, row in grouped.items() if ticket_id not in existing and row["complaint"]]
        if not missing: return
        from agents.query_agent import analyze_query
        path.parent.mkdir(parents=True, exist_ok=True)
        was_existing = path.exists()
        with path.open("a", encoding="utf-8-sig" if not was_existing else "utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            if not was_existing or path.stat().st_size == 0: writer.writeheader()
            for ticket_id, row in missing:
                analysis = analyze_query(row["complaint"])
                writer.writerow({"ticket_id": ticket_id, "timestamp": row["created_at"], "customer_name": row["customer_name"], "order_id": "", "complaint": row["complaint"], "category": analysis["category"], "urgency": analysis["urgency"], "sentiment": analysis["sentiment"], "kb_sources": "", "response": row["response"], "status": "Open", "admin_status": "Open", "admin_comment": "", "admin_updates": "[]", "escalation_reason": "Recovered from persistent chat history"})
    except (OSError, sqlite3.Error):
        return

def read_ticket_log(path):
    """Read legacy/current ticket CSVs without crashing on a malformed row."""
    if not path.exists():
        return pd.DataFrame()
    recover_missing_ticket_rows(path)
    rows = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            if not row or not row.get("ticket_id"):
                continue
            # DictReader stores surplus legacy columns under None. Ignore
            # those columns while retaining the valid ticket fields.
            rows.append({key: repair_text(value or "") for key, value in row.items() if key is not None})
    frame = pd.DataFrame(rows).fillna("")
    for column in ["ticket_id", "timestamp", "customer_name", "order_id", "complaint", "category", "urgency", "sentiment", "kb_sources", "response", "status", "admin_status", "admin_comment", "admin_updates", "escalation_reason"]:
        if column not in frame.columns: frame[column] = ""
    return frame

def repair_text(value):
    """Repair legacy UTF-8-as-Latin-1 mojibake while preserving normal text."""
    text = str(value or "")
    if any(marker in text for marker in ("à®", "à¯", "â€“", "â€”")):
        try: return text.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError): pass
    return text

def attachment_bytes(attachment):
    try:
        content = attachment["content"]
        file_path = attachment["file_path"]
    except (KeyError, TypeError, IndexError):
        content = attachment.get("content")
        file_path = attachment.get("file_path", "")
    if content:
        return bytes(content)
    path = Path(file_path)
    return path.read_bytes() if path.exists() else None

def excel_csv(dataframe):
    """Return a Tamil-safe CSV payload for direct opening in Excel."""
    export = dataframe.copy()
    for column in ("complaint", "response", "admin_comment"):
        if column in export.columns: export[column] = export[column].astype(str).str.replace("<br>", "\n", regex=False)
    return export.to_csv(index=False).encode("utf-8-sig")

load_dotenv(); st.set_page_config(page_title="Customer Support Desk", page_icon="💬", layout="wide")
st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #f8fafc 0%, #eef4fb 58%, #e8f0f7 100%); color: #243247; }
[data-testid="stSidebar"] { background: linear-gradient(180deg, #19345d 0%, #102746 100%); }
[data-testid="stSidebar"] * { color: white !important; }
[data-testid="stSidebar"] button { background: #ffffff !important; border: 1px solid #d7e3ef !important; }
[data-testid="stSidebar"] button p,
[data-testid="stSidebar"] button span,
[data-testid="stSidebar"] button div { color: #19345d !important; }
[data-testid="stSidebar"] button:hover { background: #eaf3fb !important; }
.welcome-banner { background: linear-gradient(120deg, #1d4f91 0%, #286fa6 55%, #168b94 100%); color: white; padding: 24px 28px; border-radius: 0 0 18px 18px; margin: -1rem -1rem 1.5rem; box-shadow: 0 8px 22px rgba(25,52,93,.16); }
.welcome-banner h1 { margin: 0; font-size: 2.2rem; }
.welcome-banner p { margin: 6px 0 0; opacity: .9; }
section.main > div { max-width: 1440px; padding-top: 1.5rem; }
h1, h2, h3 { letter-spacing: -0.02em; color: #1f2f46; }
h2 { margin-top: 1.5rem; }
[data-testid="stMetric"] { background: rgba(255,255,255,.88); border: 1px solid #d8e3ef; border-radius: 16px; padding: 14px 16px; box-shadow: 0 4px 16px rgba(25,52,93,.07); }
[data-testid="stMetricLabel"] { color: #746a7c; }
[data-testid="stMetricValue"] { color: #19345d; }
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button { border-radius: 10px; min-height: 2.45rem; border: 1px solid #cbd9e8; transition: all .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover { border-color: #168b94; color: #126e75; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(22,139,148,.14); }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { background: #1d6fa5; border-color: #1d6fa5; color: white; }
div[data-baseweb="input"] > div, div[data-baseweb="select"] > div, textarea, [data-testid="stFileUploaderDropzone"] { border-radius: 10px !important; border-color: #cbd9e8 !important; }
[data-testid="stExpander"] { border: 1px solid #d5e1ec; border-radius: 12px; background: rgba(255,255,255,.52); }
[data-testid="stChatMessage"] { border-radius: 16px; border: 1px solid #d5e1ec; box-shadow: 0 3px 12px rgba(25,52,93,.06); padding: .75rem 1rem; }
[data-testid="stChatInput"] { padding-bottom: 1rem; }
[data-testid="stDialog"] { border-radius: 18px !important; box-shadow: 0 18px 60px rgba(35,16,45,.25) !important; }
[data-testid="stDialog"] [data-testid="stVerticalBlock"] { gap: .65rem; }
[data-testid="stSidebar"] { box-shadow: 4px 0 18px rgba(25,52,93,.18); }
[data-testid="stSidebar"] .stRadio label { padding: 4px 8px; border-radius: 8px; }
[data-testid="stSidebar"] .stRadio label:hover { background: rgba(255,255,255,.12); }
.stAlert { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

def login_screen():
    st.markdown("<div class='welcome-banner'><h1>Customer Support Desk</h1><p>Welcome · Secure support workspace</p></div>", unsafe_allow_html=True)
    mode = st.radio("Login as", ["User", "Admin"], horizontal=True)
    if mode == "User":
        with st.form("user_login"):
            mobile = st.text_input("Mobile number")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", type="primary")
        if submitted:
            user = authenticate(mobile, password)
            if user:
                st.session_state.role = "user"; st.session_state.customer = user["name"]; st.session_state.mobile = mobile
                st.session_state.conversation_id = uuid.uuid4().hex
                st.session_state.chat_exited = False
                st.rerun()
            else: st.error("Invalid mobile number or password.")
        st.caption("Assignment demo login. Configure DEMO_USER_MOBILE and DEMO_USER_PASSWORD in .env.")
    else:
        with st.form("admin_login"):
            password = st.text_input("Admin password", type="password")
            submitted = st.form_submit_button("Admin login", type="primary")
        if submitted:
            if password == os.getenv("ADMIN_PASSWORD", "admin123"):
                st.session_state.role = "admin"; st.session_state.customer = None; st.rerun()
            else: st.error("Invalid admin password.")

def render_indexed_documents():
    index_json = Path("knowledge_base/bm25_index.json")
    index_payload = json.loads(index_json.read_text(encoding="utf-8")) if index_json.exists() else {}
    indexed_sources = index_payload.get("sources", [])
    if not indexed_sources:
        recovered_sources = {}
        for chunk in index_payload.get("chunks", []):
            source_name = chunk.get("source")
            if source_name:
                recovered_sources[source_name] = recovered_sources.get(source_name, 0) + 1
        indexed_sources = [{"source": name, "chunks": count, "sha256": hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]} for name, count in recovered_sources.items()]
    st.subheader("Indexed documents")
    st.caption(f"{len(indexed_sources)} document(s) currently included in the searchable ChromaDB knowledge base.")
    if not indexed_sources:
        st.info("No indexed documents found. Upload documents and rebuild the index.")
    for source in indexed_sources:
        source_path = Path(source["source"])
        uploaded_at = "Unknown"
        if source_path.exists():
            uploaded_at = pd.Timestamp(source_path.stat().st_mtime, unit="s").tz_localize("UTC").tz_convert("Asia/Kolkata").strftime("%d %b %Y, %I:%M %p IST")
        cols = st.columns([4.5, 1.2, 2.8, 1.1])
        cols[0].write(f"**{source_path.name}**")
        cols[1].write("Analysed")
        cols[2].write(f"Uploaded {uploaded_at}")
        if str(source["source"]).replace("\\", "/").startswith("knowledge_base/uploads/") and cols[3].button("Delete", key=f"delete_source_{source['sha256']}"):
            source_path.unlink(missing_ok=True)
            build_index(scrape=False)
            st.success("Document removed and ChromaDB rebuilt.")
            st.rerun()

def admin_panel():
    with st.sidebar:
        st.title("Welcome Admin")
        section = st.radio("Admin navigation", ["Overview", "Complaint management", "Report", "User Login management", "Knowledge management"])
        if st.button("Admin logout"): st.session_state.clear(); st.rerun()
    st.markdown(f"<div class='welcome-banner'><h1>{section}</h1><p>Welcome Admin · Support operations workspace</p></div>", unsafe_allow_html=True)
    status = api_status()
    log_path = LOG_PATH
    tickets = read_ticket_log(log_path)
    if section == "Overview":
        st.subheader("Query and complaint history")
        range_choice = st.selectbox("Date range", ["Last 7 days", "Last 30 days", "Last 90 days", "Custom date"])
        if not tickets.empty:
            ticket_dates = pd.to_datetime(tickets["timestamp"].astype(str).str.replace(" IST", "", regex=False), errors="coerce").dt.date
            today = now_ist().date()
            if range_choice == "Last 7 days": start_date = today - pd.Timedelta(days=6); end_date = today
            elif range_choice == "Last 30 days": start_date = today - pd.Timedelta(days=29); end_date = today
            elif range_choice == "Last 90 days": start_date = today - pd.Timedelta(days=89); end_date = today
            else:
                selected_dates = st.date_input("Custom date range", value=(today - pd.Timedelta(days=6), today), max_value=today)
                if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
                    start_date, end_date = selected_dates
                elif isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 1:
                    start_date = end_date = selected_dates[0]
                else:
                    start_date = end_date = selected_dates
            tickets = tickets[(ticket_dates >= start_date) & (ticket_dates <= end_date)].copy()
        if tickets.empty: st.info("No complaint activity yet.")
        else:
            status_series = tickets["admin_status"] if "admin_status" in tickets else tickets["status"]
            total = len(tickets); closed = int(status_series.isin(["Closed", "Resolved"]).sum()); open_count = total - closed
            progress = closed / total if total else 0
            cols = st.columns(5)
            cols[0].metric("Total queries", total)
            cols[1].metric("Resolved / closed", closed)
            cols[2].metric("Open", open_count)
            cols[3].metric("Customers", tickets["customer_name"].nunique())
            cols[4].metric("Resolution progress", f"{progress:.1%}")
            st.progress(progress, text=f"{progress:.1%} of recorded complaints are resolved or closed")
            left, right = st.columns(2)
            with left:
                st.subheader("Complaint status counts")
                status_df = status_series.value_counts().rename_axis("Status").reset_index(name="Complaints")
                fig = px.bar(status_df, x="Status", y="Complaints", color="Status", text="Complaints", template="simple_white")
                fig.update_layout(showlegend=False, height=330, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig, use_container_width=True)
            with right:
                st.subheader("Complaint status mix")
                fig = px.pie(status_df, names="Status", values="Complaints", hole=0.55, color="Status", template="simple_white")
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), showlegend=True)
                st.plotly_chart(fig, use_container_width=True)
            st.subheader("Category status breakdown")
            category_status = tickets.assign(Status=status_series).groupby(["category", "Status"]).size().reset_index(name="Complaints")
            fig = px.bar(category_status, x="category", y="Complaints", color="Status", barmode="stack", text="Complaints", template="simple_white", labels={"category": "Category"})
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="Status")
            st.plotly_chart(fig, use_container_width=True)
            status_table = category_status.pivot(index="category", columns="Status", values="Complaints").fillna(0).astype(int).reset_index()
            for status_name in ["Open", "In Progress", "Closed", "Rejected"]:
                if status_name not in status_table: status_table[status_name] = 0
            st.dataframe(status_table[["category", "Open", "In Progress", "Closed", "Rejected"]], use_container_width=True, hide_index=True)
            st.subheader("Urgency distribution")
            st.dataframe(tickets["urgency"].value_counts().rename_axis("urgency").reset_index(name="count"), use_container_width=True, hide_index=True)
        return
    if section == "Report":
        st.subheader("Support reports")
        if tickets.empty: st.info("No reports available yet.")
        else:
            st.download_button("Download complaint report", excel_csv(tickets), "complaint_report.csv", "text/csv")
            st.download_button("Download category summary", excel_csv(tickets["category"].value_counts().rename_axis("category").reset_index(name="count")), "category_summary.csv", "text/csv")
        return
    if section == "User Login management":
        st.subheader("Customer Login Access")
        st.caption("Disabling access preserves customer complaints and chat history.")
        complaint_counts = {}
        open_counts = {}
        if not tickets.empty and "customer_name" in tickets.columns:
            complaint_counts = tickets.groupby("customer_name").size().to_dict()
            status_field = "admin_status" if "admin_status" in tickets.columns else "status"
            open_counts = tickets[~tickets[status_field].isin(["Closed", "Resolved", "Rejected"])].groupby("customer_name").size().to_dict()
        users = list_users()
        filter_cols = st.columns([2.2, 1.4, 1.6, 1.4])
        search_term = filter_cols[0].text_input("Search customer", placeholder="Name or mobile", key="customer_search").strip().lower()
        access_filter = filter_cols[1].selectbox("Access", ["All", "Enabled", "Disabled"], key="customer_access_filter")
        sort_by = filter_cols[2].selectbox("Sort by", ["Name", "Complaints", "Open complaints"], key="customer_sort")
        sort_order = filter_cols[3].selectbox("Order", ["Ascending", "Descending"], key="customer_sort_order")
        users = [u for u in users if (not search_term or search_term in u["name"].lower() or search_term in u["mobile"])]
        if access_filter != "All": users = [u for u in users if u.get("enabled", True) == (access_filter == "Enabled")]
        descending = sort_order == "Descending"
        if sort_by == "Name": users.sort(key=lambda u: u["name"].lower(), reverse=descending)
        elif sort_by == "Complaints": users.sort(key=lambda u: complaint_counts.get(u["name"], 0), reverse=descending)
        else: users.sort(key=lambda u: open_counts.get(u["name"], 0), reverse=descending)
        enabled = [u for u in users if u.get("enabled", True)]; disabled = [u for u in users if not u.get("enabled", True)]
        st.write("**Enabled customers**")
        header = st.columns([2.8, 1.5, 1, 1, 1, 1.2])
        for column, label in zip(header, ["Customer", "Mobile", "Complaints", "Open", "Access", "Actions"]): column.markdown(f"**{label}**")
        for user in enabled:
            cols = st.columns([2.8, 1.5, 1, 1, 1, 1.2])
            total = complaint_counts.get(user["name"], 0); open_total = open_counts.get(user["name"], 0)
            cols[0].write(f"**{user['name']}**"); cols[1].write(user["mobile"])
            cols[2].write(total); cols[3].write(open_total)
            if cols[4].button("Disable", key=f"disable_{user['mobile']}"): set_enabled(user['mobile'], False); st.rerun()
            if cols[5].button("Reset pass", key=f"reset_{user['mobile']}"): st.session_state.reset_password_mobile = user["mobile"]; st.rerun()
        st.write("**Disabled customers**")
        if not disabled: st.caption("No disabled customers.")
        if disabled:
            header = st.columns([2.8, 1.5, 1, 1, 1, 1.2])
            for column, label in zip(header, ["Customer", "Mobile", "Complaints", "Open", "Access", "Actions"]): column.markdown(f"**{label}**")
        for user in disabled:
            cols = st.columns([2.8, 1.5, 1, 1, 1, 1.2])
            total = complaint_counts.get(user["name"], 0); open_total = open_counts.get(user["name"], 0)
            cols[0].write(f"**{user['name']}**"); cols[1].write(user["mobile"])
            cols[2].write(total); cols[3].write(open_total)
            if cols[4].button("Enable", key=f"enable_{user['mobile']}"): set_enabled(user['mobile'], True); st.rerun()
            if cols[5].button("Reset pass", key=f"reset_disabled_{user['mobile']}"): st.session_state.reset_password_mobile = user["mobile"]; st.rerun()
        with st.expander("Add demo customer login"):
            with st.form("add_customer"):
                name = st.text_input("Customer name"); mobile = st.text_input("Mobile number"); password = st.text_input("Password", type="password")
                if st.form_submit_button("Add customer"):
                    if name.strip() and mobile.strip() and password and add_user(name.strip(), mobile.strip(), password): st.success("Customer login added."); st.rerun()
                    else: st.error("Provide valid details and ensure the mobile number is unique.")
        if st.session_state.get("reset_password_mobile"):
            reset_mobile = st.session_state.reset_password_mobile
            reset_user = next((item for item in users if item["mobile"] == reset_mobile), None)
            if reset_user:
                st.markdown(f"**Reset password: {reset_user['name']} · {reset_user['mobile']}**")
                with st.form("reset_customer_password"):
                    new_password = st.text_input("New password", type="password")
                    confirm_password = st.text_input("Confirm new password", type="password")
                    if st.form_submit_button("Reset password"):
                        if len(new_password) < 6: st.error("Password must contain at least 6 characters.")
                        elif new_password != confirm_password: st.error("Passwords do not match.")
                        elif reset_password(reset_user["mobile"], new_password): st.session_state.pop("reset_password_mobile", None); st.success(f"Password reset for {reset_user['name']}."); st.rerun()
                        else: st.error("Customer could not be found.")
            else:
                st.error("Customer could not be found.")
        return
    if section == "Complaint management":
        st.subheader("Complaint history")
        st.caption("Click a complaint reference to view details or update its status.")
        if st.button("Refresh complaint registry", key="refresh_complaint_registry"):
            st.rerun()
        if tickets.empty: st.info("No complaints raised yet."); return
        filter_cols = st.columns([1.2, 1.2, 1.4, 1.1, 1.5])
        category_filter = filter_cols[0].selectbox("Category", ["All"] + sorted(tickets["category"].dropna().unique().tolist()), key="admin_history_category")
        status_values = sorted((tickets["admin_status"] if "admin_status" in tickets else tickets["status"]).dropna().unique().tolist())
        status_filter = filter_cols[1].selectbox("Status", ["All"] + status_values, key="admin_history_status")
        sort_field = filter_cols[2].selectbox("Sort by", ["Created", "Reference", "Category", "Priority"], key="admin_history_sort")
        sort_order = filter_cols[3].selectbox("Order", ["Ascending", "Descending"], index=1, key="admin_history_order")
        date_filter = filter_cols[4].selectbox("Date", ["All time", "Last 7 days", "Last 30 days", "Last 90 days", "Custom"], key="admin_history_date")
        if category_filter != "All": tickets = tickets[tickets["category"] == category_filter]
        if status_filter != "All": tickets = tickets[(tickets["admin_status"] if "admin_status" in tickets else tickets["status"]) == status_filter]
        if date_filter != "All time":
            today = now_ist().date()
            if date_filter == "Custom":
                date_range = st.date_input("Select date range", value=(today - pd.Timedelta(days=30), today), key="admin_history_custom_date")
                start_date, end_date = (date_range[0], date_range[-1]) if isinstance(date_range, (tuple, list)) and len(date_range) >= 2 else (date_range, date_range)
            else:
                days = int(date_filter.split()[1]); start_date, end_date = today - pd.Timedelta(days=days - 1), today
            ticket_dates = pd.to_datetime(tickets["timestamp"].astype(str).str.replace(" IST", "", regex=False), errors="coerce").dt.date
            tickets = tickets[(ticket_dates >= start_date) & (ticket_dates <= end_date)]
        sort_map = {"Created": "timestamp", "Reference": "ticket_id", "Category": "category", "Priority": "urgency"}
        tickets = tickets.sort_values(sort_map[sort_field], ascending=sort_order == "Ascending", kind="stable")
        if tickets.empty: st.info("No complaints match the selected filters."); return
        @st.dialog("Complaint details", width="large")
        def complaint_details(selected_id):
            selected = tickets[tickets["ticket_id"] == selected_id].iloc[0]
            st.write(f"**Reference:** {selected['ticket_id']}")
            st.write(f"**Customer:** {selected['customer_name']} · **Category:** {selected['category']}")
            st.write(f"**Customer sentiment:** {sentiment_rating(selected.get('sentiment', 'Neutral'))}")
            st.write(f"**Created:** {selected['timestamp']}")
            st.write(f"**Description:** {selected['complaint']}")
            admin_attachments = list_attachments(str(selected["customer_name"]), selected_id)
            if admin_attachments:
                st.subheader("Customer evidence")
                for attachment in admin_attachments:
                    payload = attachment_bytes(attachment)
                    if payload and attachment["mime_type"].startswith("image/"):
                        st.image(payload, caption=f"{attachment['filename']} · {attachment['created_at']}", width=300)
                    elif payload:
                        st.download_button("Download evidence", payload, attachment["filename"], key=f"admin_ticket_download_{attachment['id']}")
                with st.popover(f"View all {len(admin_attachments)} supporting documents"):
                    for attachment in admin_attachments:
                        payload = attachment_bytes(attachment)
                        st.caption(f"{attachment['filename']} · {attachment['created_at']}")
                        if payload and attachment["mime_type"].startswith("image/"):
                            st.image(payload, width=700)
                        elif payload:
                            st.download_button("Download document", payload, attachment["filename"], key=f"admin_all_ticket_download_{attachment['id']}")
            try: updates = json.loads(selected.get("admin_updates", "[]") or "[]")
            except json.JSONDecodeError: updates = []
            if updates:
                st.subheader("Updates")
                for update in updates:
                    st.markdown(f"**{update.get('status', 'Update')}** · {update.get('timestamp', '')}")
                    if update.get("comment"): st.info(update["comment"])
            current = selected.get("admin_status", "Open")
            statuses = ["Open", "In Progress", "Closed", "Rejected", "Cancelled"]
            new_status = st.selectbox("Complaint status", statuses, index=statuses.index(current if current in statuses else "Open"), key=f"dialog_status_{selected_id}")
            comment = st.text_area("Comment visible to the customer", selected.get("admin_comment", ""), key=f"dialog_comment_{selected_id}")
            response = st.text_area("Customer-facing response", selected["response"], key=f"dialog_response_{selected_id}")
            if st.button("Save update", type="primary", key=f"dialog_save_{selected_id}"):
                if new_status == "Rejected" and not (comment and comment.strip()):
                    st.error("Add a reason before rejecting a complaint."); return
                mapped = "Resolved" if new_status == "Closed" else selected.get("status", "Escalated")
                update_ticket(selected_id, response, mapped, new_status, comment); st.rerun()
        header = st.columns([2.1, 1.5, 1.7, 1.2, 1.8, 1.3, 1.7])
        for col, label in zip(header, ["Reference", "Customer", "Category", "Priority", "Sentiment", "Status", "Created"]): col.markdown(f"**{label}**")
        archived_statuses = {"Closed", "Resolved", "Rejected", "Cancelled", "Cancelled by customer"}
        active_tickets = tickets[~tickets["admin_status"].isin(archived_statuses)] if "admin_status" in tickets else tickets[~tickets["status"].isin(archived_statuses)]
        archived_tickets = tickets[tickets["admin_status"].isin(archived_statuses)] if "admin_status" in tickets else tickets[tickets["status"].isin(archived_statuses)]
        def render_admin_rows(rows, key_prefix):
            for _, row in rows.iterrows():
                cols = st.columns([2.1, 1.5, 1.7, 1.2, 1.8, 1.3, 1.7])
                if cols[0].button(row["ticket_id"], key=f"{key_prefix}_{row['ticket_id']}"): complaint_details(row["ticket_id"])
                cols[1].write(row.get("customer_name", ""))
                cols[2].write(row["category"])
                cols[3].write(row["urgency"])
                cols[4].write(sentiment_rating(row.get("sentiment", "Neutral")))
                cols[5].write(row.get("admin_status", row["status"]))
                cols[6].write(row["timestamp"])
        if not active_tickets.empty:
            st.subheader("Open and active tickets")
            render_admin_rows(active_tickets, "active_ticket")
        if not archived_tickets.empty:
            st.subheader("Closed, cancelled and rejected tickets")
            render_admin_rows(archived_tickets, "archived_ticket")
        return
    st.subheader("Organisation knowledge base")
    st.caption("Upload source documents, extract linked help pages, and persist searchable chunks in ChromaDB.")
    index_json = Path("knowledge_base/bm25_index.json")
    index_payload = json.loads(index_json.read_text(encoding="utf-8")) if index_json.exists() else {}
    indexed_sources = index_payload.get("sources", [])
    # Backward compatibility for indexes created before source manifests were
    # added: reconstruct the visible file list from the indexed chunks.
    if not indexed_sources:
        recovered_sources = {}
        for chunk in index_payload.get("chunks", []):
            source_name = chunk.get("source")
            if source_name:
                recovered_sources.setdefault(source_name, 0)
                recovered_sources[source_name] += 1
        indexed_sources = [{"source": source_name, "chunks": count, "sha256": hashlib.sha1(source_name.encode("utf-8")).hexdigest()[:10]} for source_name, count in recovered_sources.items()]
    with st.expander("API integration status", expanded=True):
        st.write(f"OpenAI configuration: {'Success' if status['openai_configured'] else 'Not configured'} ({status['openai_model']})")
        st.write(f"SerperDevAPI configuration: {'Success' if status['serper_configured'] else 'Not configured'}")
        st.write(f"LangSmith configuration: {'Success' if status['langsmith_configured'] else 'Not configured'}")
        st.caption("Exact provider credit balance is shown in the provider billing dashboard; this app can show tracked usage, not account balance.")
    usage_path = Path("logs/api_usage.csv")
    if usage_path.exists():
        usage = pd.read_csv(usage_path); st.subheader("API usage estimate")
        cols = st.columns(4)
        cols[0].metric("Requests", int(usage["requests"].sum()))
        cols[1].metric("Prompt tokens", int(usage["prompt_tokens"].sum()))
        cols[2].metric("Completion tokens", int(usage["completion_tokens"].sum()))
        cols[3].metric("Estimated USD", f"${usage['estimated_cost_usd'].sum():.4f}")
        st.dataframe(usage.tail(20), use_container_width=True, hide_index=True)
    uploads = st.file_uploader("Add documents", type=["docx", "pdf", "xlsx", "xlsm", "txt", "md"], accept_multiple_files=True)
    if uploads:
        folder = Path("knowledge_base/uploads"); folder.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            digest = hashlib.sha1(upload.getvalue()).hexdigest()[:10]
            (folder / f"{Path(upload.name).stem}_{digest}{Path(upload.name).suffix.lower()}").write_bytes(upload.getbuffer())
        st.success(f"Saved {len(uploads)} source document(s).")
    scrape = st.checkbox("Scrape links found inside DOCX files", value=True)
    if st.button("Build / rebuild ChromaDB index", type="primary"):
        with st.spinner("Extracting and indexing sources..."):
            payload = build_index(scrape=scrape)
        st.success(f"Indexed {len(payload['chunks'])} chunks and found {len(payload['links'])} unique links.")
    render_indexed_documents()
    report_path = Path("knowledge_base/link_report.json")
    if report_path.exists():
        rows = json.loads(report_path.read_text(encoding="utf-8"))
        if rows: st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.download_button("Download direct-link report", report_path.read_bytes(), "link_report.json", "application/json")
    if INDEX_PATH.exists(): st.caption(f"ChromaDB index: {INDEX_PATH}")
    return
    st.subheader("All customer complaints")
    if log_path.exists():
        all_tickets = read_ticket_log(log_path)
        if not all_tickets.empty:
            selected_id = st.selectbox("Select ticket to manage", all_tickets["ticket_id"].tolist(), key="admin_ticket")
            selected = all_tickets[all_tickets["ticket_id"] == selected_id].iloc[0]
            st.write(f"**Customer:** {selected['customer_name']} · **Category:** {selected['category']} · **Created:** {selected['timestamp']}")
            st.write(selected["complaint"])
            new_status = st.selectbox("Complaint status", ["Open", "In Progress", "Closed"], index=["Open", "In Progress", "Closed"].index(selected.get("admin_status", "Open") if selected.get("admin_status", "Open") in {"Open", "In Progress", "Closed"} else "Open"), key=f"status_{selected_id}")
            comment = st.text_area("Comment visible to the customer", selected.get("admin_comment", ""), key=f"comment_{selected_id}")
            if st.button("Save complaint update", type="primary", key="save_admin_update"):
                mapped = "Resolved" if new_status == "Closed" else selected.get("status", "Escalated")
                update_ticket(selected_id, selected["response"], mapped, new_status, comment)
                st.success(f"{selected_id} updated."); st.rerun()

def run_ticket(customer, complaint, progress_area=None):
    progress = []
    def callback(agent, message):
        stage_map = {"Query Agent": 1, "Knowledge Base Agent": 2, "Resolution Agent": 3, "Log Agent": 4}
        stage = stage_map.get(agent, min(len(progress) + 1, 4))
        progress.append(f"{agent}: {message}")
        if progress_area:
            bar = "█" * stage + "░" * (4 - stage)
            progress_area.markdown(f"**Support Agent · Working ({stage}/4)**  `{bar}`  **{stage * 25}%**\n\n{message}")
    conversation_id = st.session_state.setdefault("conversation_id", uuid.uuid4().hex)
    save_message(customer, "customer", complaint, conversation_id=conversation_id)
    previous_messages = list_messages(customer, conversation_id=conversation_id)[-7:]
    session_context = "\n".join(f"{str(message['role']).title()}: {str(message['content'])[:600]}" for message in previous_messages[:-1])
    result = asyncio.run(run_support_team(complaint, customer, "", callback, record_ticket=False, session_context=session_context)) or {}
    result.setdefault("ticket_id", "")
    result.setdefault("response", "I’m sorry, I could not prepare a response right now. Please try again.")
    response_text = result["response"]
    sources = list(dict.fromkeys((result.get("knowledge") or {}).get("sources", [])))
    if sources:
        source_names = ", ".join(Path(str(source)).name for source in sources[:3])
        response_text += f"\n\nSource document(s) (original): {source_names}"
    result["response"] = response_text
    st.session_state.last_knowledge_evidence = result.get("knowledge") or {}
    st.session_state.last_knowledge_conversation = conversation_id
    save_message(customer, "agent", response_text, result["ticket_id"], conversation_id)
    return result

def should_offer_complaint_review(prompt, result, has_evidence=False):
    """Open HITL complaint confirmation only for a concrete complaint."""
    if has_evidence:
        return True
    analysis = (result or {}).get("analysis") or {}
    if analysis.get("needs_clarification") or (result or {}).get("status") == "Needs clarification":
        return False
    text = str(prompt or "").lower()
    complaint_signals = (
        "complaint", "complain", "raise", "not received", "not delivered",
        "late", "broken", "damaged", "wrong item", "refund", "return",
        "charged", "payment failed", "transaction failed", "cannot login",
        "can't login", "not working", "issue", "problem"
    )
    return len(text.split()) >= 6 and any(signal in text for signal in complaint_signals)

def render_chat_bubble(speaker, content, role, timestamp):
    is_user = role == "customer"
    align = "flex-end" if is_user else "flex-start"
    bg = "#dcf8c6" if is_user else "#ffffff"
    border = "#c9e8b8" if is_user else "#dfe3e8"
    safe_speaker = html.escape(str(speaker))
    safe_content = html.escape(repair_text(content)).replace("\n", "<br>")
    safe_time = html.escape(str(timestamp))
    st.markdown(f"<div style='display:flex;flex-direction:column;align-items:{align};margin:10px 0 16px'><div style='font-weight:700;font-size:.84rem;margin:0 8px 4px'>{safe_speaker}</div><div style='background:{bg};border:1px solid {border};border-radius:16px;padding:10px 14px;max-width:82%;line-height:1.5;box-shadow:0 1px 2px rgba(0,0,0,.06)'>{safe_content}</div><div style='font-size:.7rem;color:#7c8793;margin:3px 8px'>{safe_time}</div></div>", unsafe_allow_html=True)

def casual_reply(text):
    """Return a local reply for non-support chatter, avoiding API/ticket usage."""
    normalized = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).strip()
    casual_terms = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "test", "testing", "mic check", "check mic", "good morning", "good afternoon", "good evening", "who are you"}
    if normalized in casual_terms or (len(normalized.split()) <= 5 and any(term in normalized for term in ("mic check", "check mic", "testing", "test chat"))):
        return "Hello! I’m ready to help with shipping, refund, account, product, or billing questions."
    return None

def complaint_acknowledgement(result, tamil=False):
    """Create a safe SOP-based acknowledgement without inventing an SLA."""
    evidence = str(result.get("knowledge", {}).get("summary", ""))
    match = re.search(r"(?:within|maximum|up to|turnaround)[^\n]{0,50}?(\d+)\s*(days?|hours?)", evidence, re.IGNORECASE)
    timeframe = f"within {match.group(1)} {match.group(2)}" if match else "within the timeframe specified in the applicable SOP"
    if tamil:
        tamil_timeframe = f"{match.group(1)} {('நாட்களுக்குள்' if 'day' in match.group(2).lower() else 'மணி நேரத்திற்குள்')}" if match else "SOP-ல் குறிப்பிடப்பட்டுள்ள காலக்கெடுவிற்குள்"
        return f"உங்கள் புகார் பதிவு செய்யப்பட்டு, தொடர்புடைய SOP-ன் அடிப்படையில் பதிவு செய்யப்பட்டுள்ளது. எங்கள் ஆதரவு குழு {tamil_timeframe} பதிலளித்து, தொடர்ந்து புதுப்பிப்புகளை வழங்கும்."
    return f"Your complaint has been logged under the applicable SOP. Our support team will respond {timeframe} and keep you updated."

def sentiment_rating(sentiment):
    scores = {"Angry": 1, "Frustrated": 2, "Neutral": 3, "Happy": 5}
    score = scores.get(str(sentiment), 3)
    return f"{sentiment or 'Neutral'} · {'★' * score}{'☆' * (5 - score)} ({score}/5)"

def transcribe_voice(audio_file):
    """Transcribe English, Tamil, or Tanglish with automatic language detection."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured for voice transcription.")
    from openai import OpenAI
    suffix = Path(audio_file.name or "voice.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        temp.write(audio_file.getvalue())
        temp_path = temp.name
    try:
        prompt = (
            "Transcribe word-for-word and preserve the speaker's language and script. "
            "Tamil words must remain in Tamil script. English words must remain in English. "
            "If the speaker mixes Tamil and English (Tanglish), keep that same mixed form. "
            "For example, spoken 'எனக்கு ஒரு complaint raise பண்ண வேண்டும்' must be written "
            "as 'எனக்கு ஒரு complaint raise பண்ண வேண்டும்', not converted to Tamil script. "
            "Detect only Tamil, English, or mixed Tamil-English (Tanglish). Do not select or output Hindi or any other language. Do not translate, transliterate, or normalize "
            "Tamil into English. Do not add explanations."
        )
        with open(temp_path, "rb") as audio:
            result = OpenAI(api_key=api_key).audio.transcriptions.create(
                model=os.getenv("AUDIO_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
                file=audio,
                prompt=prompt,
            )
        transcript = (result.text or "").strip()
        # The voice feature is intentionally limited to Tamil, English, and
        # Tanglish. Reject another script instead of placing it in the composer.
        unsupported_scripts = r"[\u0900-\u097F\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF\u0980-\u09FF\u0C00-\u0C7F\u0D00-\u0D7F\u0C80-\u0CFF]"
        if re.search(unsupported_scripts, transcript):
            raise ValueError("Voice transcription detected an unsupported language. Please speak in Tamil or English.")
        return transcript
    finally:
        try: os.unlink(temp_path)
        except OSError: pass

def complaint_evidence_prompt(customer, summary):
    """Show proportionate evidence checks before converting chat into a ticket."""
    from agents.query_agent import analyze_query
    analysis = analyze_query(summary or "")
    category = analysis["category"]
    raw_issue = analysis.get("summary") or str(summary or "").strip()
    polished_draft = f"Customer reports a {category.lower()} issue: {raw_issue}"
    summarized_complaint = st.text_area(
        "Targeted complaint summary",
        value=polished_draft[:3000],
        height=150,
        help="Keep only the specific issue. Remove greetings and unrelated chat.",
        key="chat_complaint_summary",
    )
    requirements = {
        "Billing": "Payment failure or incorrect charge: request a payment/error screenshot or transaction proof.",
        "Product": "Damaged or defective product: request a product photo. A bill/order proof is also useful.",
        "Refund": "Refund/return issue: request the bill, order proof, or refund-status screenshot.",
        "Shipping": "Delivery delay: product photos are not required; order/delivery details are sufficient.",
        "Account": "Account issue: ask for an error screenshot only if it helps; never request passwords or OTPs.",
    }
    st.subheader("Analyze and raise complaint")
    st.caption("The agent extracts the targeted issue, keeps the complaint crisp, and requests only relevant evidence before creating a ticket.")
    st.info(f"Detected issue type: **{category}** — {requirements[category]}")
    st.caption(f"Customer tone: {analysis['sentiment']} · Urgency: {analysis['urgency']}")
    evidence_label = "Evidence available (optional)"
    if category in {"Billing", "Product", "Refund"}: evidence_label = "Evidence available"
    evidence_note = st.text_input("Evidence note", placeholder="Example: payment failed at 10:30 AM; order ending 1234")
    confirm = st.checkbox("I confirm this complaint summary is accurate and contains no password, OTP, or other secret", key="chat_complaint_confirm")
    submit = st.button("Analyze chat and raise complaint", type="primary", disabled=not confirm, key="raise_from_chat")
    if submit:
        if not str(summarized_complaint or "").strip():
            st.warning("Please provide a complaint summary before confirming.")
            return
        existing_evidence = list_attachments(customer)
        if category in {"Billing", "Product", "Refund"} and not existing_evidence and not str(evidence_note or "").strip():
            st.warning("Please attach relevant evidence or add a short evidence note before submitting this issue.")
            return
        evidence = []
        if existing_evidence: evidence.append(f"Evidence already attached in chat: {len(existing_evidence)} file(s)")
        if str(evidence_note or "").strip(): evidence.append(f"Evidence note: {str(evidence_note).strip()}")
        complaint = f"Chat-consolidated complaint ({category}).\nConfirmed complaint summary: {str(summarized_complaint or '').strip()[:3000]}\nCustomer tone: {analysis['sentiment']}\nUrgency: {analysis['urgency']}"
        if evidence: complaint += "\n" + "\n".join(evidence)
        result = run_ticket(customer=customer, complaint=complaint, progress_area=st.empty())
        st.success(f"Complaint {result['ticket_id']} has been registered.")
        if re.search(r"[\u0B80-\u0BFF]", summarized_complaint):
            st.info(complaint_acknowledgement(result, tamil=True))
        else:
            st.info(complaint_acknowledgement(result))
        st.session_state["chat_complaint_confirm"] = False
        st.rerun()

def customer_page():
    customer = st.session_state.get("customer", "Demo User")
    customer_display = html.escape(str(customer))
    with st.sidebar:
        st.title(f"Welcome {customer_display}")
        if st.button("Log out"): st.session_state.clear(); st.rerun()
        with st.expander("Reset password"):
            with st.form("customer_reset_password"):
                current_password = st.text_input("Current password", type="password")
                new_password = st.text_input("New password", type="password")
                confirm_password = st.text_input("Confirm new password", type="password")
                if st.form_submit_button("Update password"):
                    if not authenticate(st.session_state.get("mobile", ""), current_password):
                        st.error("Current password is incorrect.")
                    elif len(new_password) < 6:
                        st.error("Password must contain at least 6 characters.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    elif reset_password(st.session_state.get("mobile", ""), new_password):
                        st.success("Password updated successfully.")
                    else:
                        st.error("Password could not be updated.")
        pages = ["Chat with Agent", "Complaint Tracker"]
        pending_page = st.session_state.pop("pending_customer_page", None)
        page = st.radio("Navigate", pages, index=pages.index(pending_page) if pending_page in pages else 0)
        previous_page = st.session_state.get("last_customer_page")
        if previous_page != page:
            st.session_state.customer_ticket_popup = False
            st.session_state.pop("selected_customer_ticket", None)
            if page == "Chat with Agent":
                # Start a clean chat when entering the page. Older chats remain
                # available through the explicit Conversation history action.
                st.session_state.conversation_id = uuid.uuid4().hex
                st.session_state.chat_exited = False
        st.session_state.last_customer_page = page
        st.divider(); st.caption("All timestamps use IST")
    customer = st.session_state.customer; log_path = LOG_PATH
    conversation_id = st.session_state.setdefault("conversation_id", uuid.uuid4().hex)
    tickets = read_ticket_log(log_path)
    if not tickets.empty and "customer_name" in tickets.columns:
        customer_key = str(customer).strip().casefold()
        customer_tickets = tickets[tickets["customer_name"].astype(str).str.strip().str.casefold() == customer_key].copy()
    else:
        customer_tickets = tickets
    registration_flash = st.session_state.pop("registration_flash", None)
    if registration_flash:
        st.success(registration_flash["success"])
        st.info(registration_flash["info"])
    if page == "Chat with Agent":
        st.markdown(f"<div class='welcome-banner'><h1>Support Desk</h1><p>Welcome, {customer_display} · Customer support workspace</p></div>", unsafe_allow_html=True)
        st.caption("Ask questions and continue chatting while support is reviewing an open ticket.")
        history_col, new_col = st.columns([3, 1])
        conversations = list_conversations(customer)
        if history_col.button(f"Conversation history ({len(conversations)})", use_container_width=True, key="toggle_chat_history"):
            st.session_state.chat_history_panel_open = not st.session_state.get("chat_history_panel_open", False)
        if new_col.button("＋ New chat", use_container_width=True, key="new_chat"):
            st.session_state.conversation_id = uuid.uuid4().hex
            st.session_state.chat_history_panel_open = False
            st.session_state.chat_exited = False
            st.rerun()
        if st.session_state.get("chat_history_panel_open"):
            with st.expander("Recent conversations", expanded=True):
                for item in conversations:
                    label = f"{str(item['started_at']).split(' ')[0]} · {item['message_count']} messages"
                    if st.button(label, key=f"conversation_{item['conversation_id']}", use_container_width=True):
                        st.session_state.conversation_id = item["conversation_id"]
                        st.session_state.chat_history_panel_open = False
                        st.session_state.chat_exited = False
                        st.session_state.pop("voice_draft", None)
                        st.rerun()
        conversation_id = st.session_state.setdefault("conversation_id", uuid.uuid4().hex)
        chat_messages = list_messages(customer, conversation_id=conversation_id)
        for msg in chat_messages:
            render_chat_bubble(customer if msg["role"] == "customer" else "Support Agent", msg["content"], msg["role"], msg["created_at"])
        if st.session_state.get("last_knowledge_conversation") == conversation_id:
            evidence = st.session_state.get("last_knowledge_evidence", {})
            evidence_text = str(evidence.get("summary", "")).strip()
            evidence_sources = list(dict.fromkeys(evidence.get("sources", []) or []))
            if evidence_text and evidence_sources:
                with st.expander("Knowledge-base evidence used for this response"):
                    st.caption("Original retrieved content used by the agents; source names are preserved as indexed.")
                    st.write(evidence_text)
                    st.write("**Source document(s) (original):** " + ", ".join(Path(str(source)).name for source in evidence_sources))
        customer_messages = [repair_text(msg["content"]) for msg in chat_messages if msg["role"] == "customer"]
        complaint_intent = any(term in " ".join(customer_messages[-3:]).lower() for term in ("complaint", "complain", "raise", "refund", "charged", "payment failed", "broken", "damaged", "not delivered", "late delivery"))
        if complaint_intent and st.button("Raise complaint from this chat", key="raise_from_chat_nav"):
            st.session_state.raise_from_chat_open = True
            st.rerun()
        if False and st.session_state.get("raise_from_chat_open", False):
            st.subheader("Raise complaint from this chat")
            st.caption("Only the current conversation is used. Review the complaint details before submitting.")
            from agents.query_agent import analyze_query, sentiment_from_conversation
            history_payload = [dict(message) for message in chat_messages]
            raw_complaint = asyncio.run(summarize_complaint_history(history_payload, customer))
            complaint_analysis = analyze_query(raw_complaint)
            complaint_draft = raw_complaint[:1200]
            complaint = st.text_area("Concise complaint details", value=complaint_draft[:1200], height=120, key="current_complaint_details", help="This is a focused complaint summary, not a transcript of the chat.")
            evidence_uploads = st.file_uploader("Attach validation evidence (if requested by the agent)", type=["png", "jpg", "jpeg", "pdf", "doc", "docx"], accept_multiple_files=True, key="current_complaint_evidence")
            submit_complaint = st.button("Submit complaint", type="primary", disabled=not complaint.strip(), key="submit_current_complaint")
            if st.button("Cancel complaint", key="cancel_current_complaint"):
                st.session_state.raise_from_chat_open = False
                st.rerun()
            if submit_complaint:
                result = register_confirmed_complaint(complaint.strip(), customer)
                save_message(customer, "customer", complaint.strip(), result["ticket_id"], conversation_id)
                save_message(customer, "agent", complaint_acknowledgement(result, tamil=bool(re.search(r"[\u0B80-\u0BFF]", complaint))), result["ticket_id"], conversation_id)
                for evidence in evidence_uploads or []:
                    save_attachment(customer, result["ticket_id"], evidence.name, evidence.getvalue(), evidence.type, conversation_id)
                st.session_state.registration_flash = {"success": f"Complaint {result['ticket_id']} has been registered.", "info": complaint_acknowledgement(result, tamil=bool(re.search(r"[\u0B80-\u0BFF]", complaint)))}
                st.session_state.raise_from_chat_open = False
                st.session_state.pending_customer_page = "Complaint Tracker"
                st.rerun()
            return
        if st.session_state.get("raise_from_chat_open", False):
            from agents.query_agent import analyze_query, sentiment_from_conversation
            # Build the registration draft from the complete current conversation.
            # Do not use the last message: it is often only a confirmation, a
            # question about the ticket, or a request to raise a complaint.
            summary_key = f"{customer}:{conversation_id}"
            if st.session_state.get("complaint_summary_key") != summary_key:
                history_payload = [dict(message) for message in chat_messages]
                raw_complaint = asyncio.run(summarize_complaint_history(history_payload, customer))
                st.session_state.complaint_conversation_sentiment = sentiment_from_conversation(history_payload)
                if not str(raw_complaint or "").strip():
                    raw_complaint = "Please describe the specific problem you want to report."
                st.session_state.complaint_summary_draft = str(raw_complaint).strip()[:1200]
                st.session_state.complaint_summary_key = summary_key
            complaint_draft = st.session_state.get("complaint_summary_draft", "")

            @st.dialog("Review complaint before registration", width="large")
            def review_complaint_popup():
                st.caption("Only the focused complaint summary and supporting evidence are shown here.")
                complaint = st.text_area("Senior Editor complaint summary", value=complaint_draft[:1200], height=140, key="popup_complaint_summary")
                chat_attachments = [a for a in list_attachments(customer, conversation_id=conversation_id) if not a["ticket_id"]]
                selected_chat_attachments = []
                if chat_attachments:
                    st.write("**Forward from chat**")
                    for attachment in chat_attachments:
                        payload = attachment_bytes(attachment)
                        chosen = st.checkbox(attachment["filename"], value=True, key=f"forward_attachment_{attachment['id']}")
                        if chosen: selected_chat_attachments.append(attachment)
                        if payload and attachment["mime_type"].startswith("image/"):
                            st.image(payload, width=180)
                evidence_uploads = st.file_uploader("Supporting documents", type=["png", "jpg", "jpeg", "pdf", "doc", "docx"], accept_multiple_files=True, key="popup_complaint_evidence")
                if evidence_uploads:
                    st.caption(f"{len(evidence_uploads)} supporting file(s) selected. Files will be compressed and saved with the ticket.")
                _, cancel_col, confirm_col = st.columns([5, 1.2, 1.2])
                if cancel_col.button("Cancel", key="popup_cancel_complaint", help="Cancel complaint review"):
                    st.session_state.raise_from_chat_open = False
                    st.session_state.pop("popup_complaint_summary", None)
                    st.session_state.pop("complaint_summary_draft", None)
                    st.session_state.pop("complaint_summary_key", None)
                    st.rerun()
                if confirm_col.button("OK", type="primary", key="popup_confirm_complaint", help="Confirm and register complaint"):
                    if not complaint.strip():
                        st.error("Complaint summary cannot be empty.")
                        return
                    result = register_confirmed_complaint(complaint.strip(), customer, sentiment_override=st.session_state.get("complaint_conversation_sentiment", "Neutral"))
                    save_message(customer, "customer", complaint.strip(), result["ticket_id"], conversation_id)
                    save_message(customer, "agent", complaint_acknowledgement(result, tamil=bool(re.search(r"[\u0B80-\u0BFF]", complaint))), result["ticket_id"], conversation_id)
                    # Persist newly selected files and explicitly link the
                    # evidence already uploaded in this conversation. This
                    # keeps attachments scoped to this chat session and makes
                    # them available from both customer and admin history.
                    for evidence in evidence_uploads or []:
                        save_attachment(customer, result["ticket_id"], evidence.name, evidence.getvalue(), evidence.type, conversation_id)
                    for attachment in selected_chat_attachments:
                        link_attachment(attachment["id"], result["ticket_id"])
                    st.session_state.registration_flash = {"success": f"Complaint {result['ticket_id']} has been registered.", "info": complaint_acknowledgement(result, tamil=bool(re.search(r"[\u0B80-\u0BFF]", complaint)))}
                    st.session_state.raise_from_chat_open = False
                    st.session_state.pending_customer_page = "Complaint Tracker"
                    st.session_state.selected_customer_ticket = result["ticket_id"]
                    st.session_state.pop("popup_complaint_summary", None)
                    st.session_state.pop("complaint_summary_draft", None)
                    st.session_state.pop("complaint_summary_key", None)
                    st.rerun()
            review_complaint_popup()
        attachments = list_attachments(customer, conversation_id=conversation_id)
        if attachments:
            with st.expander("Chat evidence and attachments"):
                for attachment in attachments:
                    st.caption(f"{attachment['ticket_id']} · {attachment['filename']} · {attachment['created_at']}")
                    payload = attachment_bytes(attachment)
                    if payload and attachment["mime_type"].startswith("image/"):
                        st.image(payload, width=220)
                    elif payload:
                        st.download_button("Download evidence", payload, attachment["filename"], key=f"download_{attachment['id']}")
        st.caption("WhatsApp-style composer · type, drag and drop evidence, or use the microphone · language auto-detected")
        if st.button("Exit chat", key="exit_chat"):
            st.session_state.chat_exited = True
            st.rerun()
        if st.session_state.get("chat_exited", False):
            st.info("You have exited this chat. Your conversation history is saved. Select New chat to continue.")
            return
        voice_draft = st.session_state.get("voice_draft", "")
        if False and voice_draft:
            st.markdown("**Transcribed message — review before sending**")
            reviewed_voice = st.text_area("Message text", value=voice_draft, height=100, key="review_voice_text")
            confirm_col, cancel_col, _ = st.columns([0.18, 0.18, 1.64])
            if confirm_col.button("✓", type="primary", key="confirm_voice_text", help="Send transcription"):
                if reviewed_voice.strip():
                    st.session_state.pop("voice_draft", None)
                    st.session_state.pop("review_voice_text", None)
                    render_chat_bubble(customer, reviewed_voice.strip(), "customer", now_ist().strftime("%Y-%m-%d %H:%M:%S IST"))
                    casual = casual_reply(reviewed_voice.strip())
                    if casual:
                        save_message(customer, "customer", reviewed_voice.strip(), conversation_id=conversation_id)
                        save_message(customer, "agent", casual, conversation_id=conversation_id)
                    else:
                        result = run_ticket(customer, reviewed_voice.strip(), st.empty())
                        if should_offer_complaint_review(reviewed_voice.strip(), result):
                            st.session_state.raise_from_chat_open = True
                    st.rerun()
            if cancel_col.button("✕", key="cancel_voice_text", help="Discard transcription"):
                st.session_state.pop("voice_draft", None)
                st.session_state.pop("review_voice_text", None)
                st.rerun()
            st.caption("Edit the text if needed, then confirm to send it to the agent.")
        if voice_draft:
            @st.dialog("Review transcribed message")
            def review_voice_popup():
                st.caption("Edit the transcription before sending it to the agent.")
                reviewed_voice = st.text_area("Message text", value=voice_draft, height=140, key="popup_voice_text")
                _, cancel_col, confirm_col = st.columns([5, 1.2, 1.2])
                if confirm_col.button("OK", type="primary", key="popup_confirm_voice", help="Use this transcription as the message"):
                    if reviewed_voice and reviewed_voice.strip():
                        # Human review only: put the edited transcription into
                        # the composer. The agent must not run until Send is
                        # pressed from the normal chat bar.
                        st.session_state.chat_composer = reviewed_voice.strip()
                        st.session_state.pop("voice_draft", None)
                        st.session_state.pop("popup_voice_text", None)
                        st.rerun()
                if cancel_col.button("Cancel", key="popup_cancel_voice", help="Discard transcription"):
                    st.session_state.pop("voice_draft", None)
                    st.session_state.pop("popup_voice_text", None)
                    st.rerun()
            review_voice_popup()
        chat_value = st.chat_input(
            "Type a message, attach evidence, or record a voice note…",
            key="chat_composer",
            accept_file=True,
            file_type=["png", "jpg", "jpeg", "pdf", "doc", "docx"],
            accept_audio=True,
            max_upload_size=20,
        )
        if chat_value:
            prompt = getattr(chat_value, "text", chat_value) or ""
            files = list(getattr(chat_value, "files", []) or [])
            evidence_files = []
            audio_upload = getattr(chat_value, "audio", None)
            if audio_upload is not None:
                try:
                    st.session_state.voice_draft = transcribe_voice(audio_upload)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Voice transcription unavailable: {type(exc).__name__}")
                    prompt = ""
            audio_files = [uploaded for uploaded in files if getattr(uploaded, "type", "").startswith("audio/")]
            if audio_files:
                try:
                    st.session_state.voice_draft = transcribe_voice(audio_files[0])
                    st.rerun()
                except Exception as exc:
                    st.error(f"Voice transcription unavailable: {type(exc).__name__}")
            for uploaded in files:
                if getattr(uploaded, "type", "").startswith("audio/"):
                    continue
                else:
                    evidence_files.append(uploaded)
            if prompt.strip() or evidence_files:
                if evidence_files:
                    prompt = (prompt.strip() + "\nEvidence attached: " + ", ".join(f.name for f in evidence_files)).strip()
                render_chat_bubble(customer, prompt, "customer", now_ist().strftime("%Y-%m-%d %H:%M:%S IST"))
                casual = casual_reply(prompt) if not evidence_files else None
                if casual:
                    save_message(customer, "customer", prompt, conversation_id=conversation_id)
                    save_message(customer, "agent", casual, conversation_id=conversation_id)
                else:
                    progress_area = st.empty()
                    result = run_ticket(customer, prompt, progress_area)
                    for uploaded in evidence_files:
                        save_attachment(customer, result["ticket_id"], uploaded.name, uploaded.getvalue(), uploaded.type, conversation_id)
                    if should_offer_complaint_review(prompt, result, bool(evidence_files)):
                        st.session_state.raise_from_chat_open = True
                st.rerun()
    elif page == "Complaint Raiser":
        st.title("Raise a complaint"); st.caption("Only your complaint statements are included below. Review the focused issue before submitting.")
        customer_messages = [repair_text(msg["content"]) for msg in list_messages(customer) if msg["role"] == "customer"]
        summary = " ".join(customer_messages[-3:])
        complaint = st.text_area("Complaint details", value=summary[:3000], height=180)
        evidence_uploads = st.file_uploader("Attach validation evidence (if requested by the agent)", type=["png", "jpg", "jpeg", "pdf", "doc", "docx"], accept_multiple_files=True)
        if st.button("Submit complaint", type="primary", disabled=not complaint.strip()):
            result = run_ticket(customer, complaint.strip(), st.empty())
            for evidence in evidence_uploads or []:
                save_attachment(customer, result["ticket_id"], evidence.name, evidence.getvalue(), evidence.type, conversation_id)
            st.success(f"Complaint {result['ticket_id']} has been registered.")
            if re.search(r"[\u0B80-\u0BFF]", complaint):
                st.info(complaint_acknowledgement(result, tamil=True))
            else:
                st.info(complaint_acknowledgement(result))
    elif page == "Complaint Tracker":
        st.title("Complaint history")
        if customer_tickets.empty: st.info("No complaints found.")
        else:
            filter_cols = st.columns([1.2, 1.2, 1.4, 1.1, 1.5])
            category_filter = filter_cols[0].selectbox("Category", ["All"] + sorted(customer_tickets["category"].dropna().unique().tolist()), key="customer_history_category")
            customer_status = customer_tickets["admin_status"] if "admin_status" in customer_tickets else customer_tickets["status"]
            status_filter = filter_cols[1].selectbox("Status", ["All"] + sorted(customer_status.dropna().unique().tolist()), key="customer_history_status")
            sort_field = filter_cols[2].selectbox("Sort by", ["Submitted", "Reference", "Category", "Status"], key="customer_history_sort")
            sort_order = filter_cols[3].selectbox("Order", ["Ascending", "Descending"], index=1, key="customer_history_order")
            date_filter = filter_cols[4].selectbox("Date", ["All time", "Last 7 days", "Last 30 days", "Last 90 days", "Custom"], key="customer_history_date")
            if category_filter != "All": customer_tickets = customer_tickets[customer_tickets["category"] == category_filter]
            if status_filter != "All": customer_tickets = customer_tickets[customer_status == status_filter]
            if date_filter != "All time":
                today = now_ist().date()
                if date_filter == "Custom":
                    date_range = st.date_input("Custom date range", value=(today - pd.Timedelta(days=6), today), key="customer_history_custom_date")
                    start_date, end_date = (date_range[0], date_range[1]) if isinstance(date_range, (tuple, list)) and len(date_range) == 2 else (date_range[0], date_range[0]) if isinstance(date_range, (tuple, list)) and len(date_range) == 1 else (date_range, date_range)
                else:
                    days = int(date_filter.split()[1]); start_date, end_date = today - pd.Timedelta(days=days - 1), today
                ticket_dates = pd.to_datetime(customer_tickets["timestamp"].astype(str).str.replace(" IST", "", regex=False), errors="coerce").dt.date
                customer_tickets = customer_tickets[(ticket_dates >= start_date) & (ticket_dates <= end_date)]
            sort_map = {"Submitted": "timestamp", "Reference": "ticket_id", "Category": "category"}
            if sort_field == "Status": customer_tickets = customer_tickets.assign(_sort_status=(customer_tickets["admin_status"] if "admin_status" in customer_tickets else customer_tickets["status"])).sort_values("_sort_status", ascending=sort_order == "Ascending", kind="stable").drop(columns=["_sort_status"])
            else: customer_tickets = customer_tickets.sort_values(sort_map[sort_field], ascending=sort_order == "Ascending", kind="stable")
            if customer_tickets.empty: st.info("No complaints match the selected filters."); return
            status_col = "admin_status" if "admin_status" in customer_tickets.columns else "status"
            st.caption("Your complaints and their current status.")
            status_col = "admin_status" if "admin_status" in customer_tickets.columns else "status"
            header = st.columns([2.8, 1.8, 2.4, 1.8, 2.2])
            for col, label in zip(header, ["Reference", "Category", "Customer sentiment", "Status", "Submitted"]): col.markdown(f"**{label}**")
            archived_statuses = {"Closed", "Resolved", "Rejected", "Cancelled", "Cancelled by customer"}
            active_tickets = customer_tickets[~customer_tickets[status_col].isin(archived_statuses)]
            archived_tickets = customer_tickets[customer_tickets[status_col].isin(archived_statuses)]
            def render_customer_rows(rows, key_prefix):
                for _, row in rows.iterrows():
                    cols = st.columns([2.8, 1.8, 2.4, 1.8, 2.2])
                    if cols[0].button(row["ticket_id"], key=f"{key_prefix}_{row['ticket_id']}", use_container_width=True):
                        st.session_state.selected_customer_ticket = row["ticket_id"]
                        st.session_state.customer_ticket_popup = True
                        st.rerun()
                    cols[1].write(row["category"])
                    cols[2].write(sentiment_rating(row.get("sentiment", "Neutral")))
                    cols[3].write(row.get(status_col, row.get("status", "Open")))
                    cols[4].write(row["timestamp"])
            if not active_tickets.empty:
                st.subheader("Open and active complaints")
                render_customer_rows(active_tickets, "customer_active_ticket")
            if not archived_tickets.empty:
                st.subheader("Closed, cancelled and rejected complaints")
                render_customer_rows(archived_tickets, "customer_archived_ticket")
            selected_id = st.session_state.get("selected_customer_ticket")
            if selected_id not in customer_tickets["ticket_id"].tolist(): selected_id = None
            selected = customer_tickets[customer_tickets["ticket_id"] == selected_id].iloc[0] if selected_id else None
            if False:  # Ticket details are shown in the popup below.
                st.write(f"**Reference:** {selected['ticket_id']}")
                st.write(f"**Category:** {selected['category']} · **Status:** {selected['status']}")
                st.write(f"**Customer sentiment:** {sentiment_rating(selected.get('sentiment', 'Neutral'))} · **Urgency:** {selected.get('urgency', 'Low')}")
                st.write(f"**Submitted:** {selected['timestamp']}")
                st.write(f"**Description:** {selected['complaint']}")
                display_status = selected.get(status_col, selected.get("status", "Open"))
                st.write(f"**Current status:** {display_status}")
                try: updates = json.loads(selected.get("admin_updates", "[]") or "[]")
                except (TypeError, json.JSONDecodeError): updates = []
                if updates:
                    latest = updates[0]
                    st.info(f"{latest.get('comment') or 'Status updated to ' + latest.get('status', display_status)}\n\n{latest.get('timestamp', '')}")
                elif selected.get("admin_comment", ""):
                    st.info(f"Support update: {selected['admin_comment']}")
                else:
                    st.caption("Awaiting support response.")
                if display_status not in {"Closed", "Resolved", "Rejected", "Cancelled"}:
                    if st.button("Cancel this complaint", key=f"cancel_customer_{selected_id}"):
                        update_ticket(selected_id, status="Cancelled", admin_status="Cancelled", admin_comment="Cancelled by customer")
                        st.rerun()
                attachments = list_attachments(customer, selected_id)
                if attachments:
                    st.write("**Evidence attached to this complaint**")
                    for attachment in attachments:
                        payload = attachment_bytes(attachment)
                        if payload and attachment["mime_type"].startswith("image/"):
                            st.image(payload, caption=f"{attachment['filename']} · {attachment['created_at']}", width=260)
                        elif payload:
                            st.download_button("Download evidence", payload, attachment["filename"], key=f"ticket_download_{attachment['id']}")
            @st.dialog("Complaint details", width="large")
            def show_customer_ticket(ticket_id):
                item = customer_tickets[customer_tickets["ticket_id"] == ticket_id].iloc[0]
                display_status = item.get(status_col, item.get("status", "Open"))
                st.write(f"**Reference:** {item['ticket_id']}")
                st.write(f"**Category:** {item['category']} · **Status:** {display_status}")
                st.write(f"**Customer sentiment:** {sentiment_rating(item.get('sentiment', 'Neutral'))} · **Urgency:** {item.get('urgency', 'Low')}")
                st.write(f"**Submitted:** {item['timestamp']}")
                st.write(f"**Complaint:** {item['complaint']}")
                try: updates = json.loads(item.get("admin_updates", "[]") or "[]")
                except (TypeError, json.JSONDecodeError): updates = []
                if updates:
                    latest = updates[0]
                    st.info(f"{latest.get('comment') or 'Status updated to ' + latest.get('status', display_status)}\n\n{latest.get('timestamp', '')}")
                elif item.get("admin_comment", ""):
                    st.info(f"Support update: {item['admin_comment']}")
                else:
                    st.caption("Awaiting support response.")
                attachments = list_attachments(customer, ticket_id)
                if attachments:
                    st.write("**Supporting documents**")
                    for attachment in attachments:
                        payload = attachment_bytes(attachment)
                        if payload and attachment["mime_type"].startswith("image/"):
                            st.image(payload, caption=f"{attachment['filename']} · {attachment['created_at']}", width=320)
                        elif payload:
                            st.download_button("Download evidence", payload, attachment["filename"], key=f"popup_ticket_download_{attachment['id']}")
                if display_status not in {"Closed", "Resolved", "Rejected", "Cancelled"}:
                    if st.button("Cancel this complaint", key=f"popup_cancel_customer_{ticket_id}"):
                        update_ticket(ticket_id, status="Cancelled", admin_status="Cancelled", admin_comment="Cancelled by customer")
                        st.session_state.customer_ticket_popup = False
                        st.rerun()
            if selected_id and st.session_state.get("customer_ticket_popup", False):
                show_customer_ticket(selected_id)
            if (customer_tickets["status"] == "Escalated").any(): st.warning("Open tickets are awaiting an admin response. You can continue chatting for context.")
    else:
        st.title("Complaint Tracker"); st.caption("Your complaint history and current support status.")
        status_col = "admin_status" if "admin_status" in customer_tickets.columns else "status"
        open_t = customer_tickets[customer_tickets[status_col].isin(["Open", "In Progress", "Escalated"])] if not customer_tickets.empty else customer_tickets
        closed_t = customer_tickets[customer_tickets[status_col].isin(["Closed", "Resolved", "Rejected"])] if not customer_tickets.empty else customer_tickets
        cols = st.columns(3); cols[0].metric("Total complaints", len(customer_tickets)); cols[1].metric("Open", len(open_t)); cols[2].metric("Closed", len(closed_t))
        st.subheader("Status overview")
        if customer_tickets.empty:
            st.info("No complaint activity yet. Start a chat if you need support.")
        else:
            status_summary = customer_tickets[status_col].value_counts().rename_axis("Status").reset_index(name="Count")
            st.dataframe(status_summary, use_container_width=True, hide_index=True)
            st.subheader("Support activity")
            st.caption(f"{len(list_messages(customer))} chat message(s) stored for {customer}.")
            st.caption(f"{len(open_t)} complaint(s) currently awaiting support action.")
            st.info("Open Complaint Tracker to view a ticket, read support updates, cancel an open request, or review attached evidence.")

if "role" not in st.session_state:
    login_screen(); st.stop()
if st.session_state.role == "admin": admin_panel()
else: customer_page()
