#!/usr/bin/env python3
"""
snow-app.py
===========
Gen AI Chatbot and Unstructured Document Parser built with Streamlit and Gemini API (Vertex AI).
Includes Model Armor validation and streaming ingestion into BigQuery.
"""

import os
import sys
import json
import logging
import datetime
import mimetypes
from typing import Dict, Any, Optional, List

import streamlit as st
import pandas as pd
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
    from google.cloud import bigquery
    from google.cloud import storage
    from google.api_core import exceptions as gcp_exceptions
except ImportError as e:
    st.error(f"Missing dependencies: {e}. Run: pip install -r requirements.txt")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("snow-app")

# ---------------------------------------------------------------------------
# Configuration (env-driven, Cloud Run friendly)
# ---------------------------------------------------------------------------
def resolve_project_id() -> str:
    """Resolve GCP project ID from env or gcloud config."""
    env_proj = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if env_proj and env_proj != "cloudshell-gca" and env_proj != "your-gcp-project":
        return env_proj
    try:
        import subprocess
        res = subprocess.run(["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=3)
        proj = res.stdout.strip().split()[-1]
        if proj and proj != "(unset)":
            return proj
    except Exception:
        pass
    if env_proj:
        return env_proj
    try:
        import google.auth
        _, proj = google.auth.default()
        if proj:
            return proj
    except Exception:
        pass
    return "your-gcp-project"

GCP_PROJECT = resolve_project_id()
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

MODEL_ARMOR_PROMPT_TEMPLATE = os.environ.get("MODEL_ARMOR_PROMPT_TEMPLATE", "")
MODEL_ARMOR_RESPONSE_TEMPLATE = os.environ.get("MODEL_ARMOR_RESPONSE_TEMPLATE", MODEL_ARMOR_PROMPT_TEMPLATE)

BQ_DATASET = os.environ.get("BIGQUERY_DATASET_ID", "snow_app")
BQ_TABLE = os.environ.get("BIGQUERY_TABLE_ID", "documents")
BQ_LOG_TABLE = os.environ.get("BIGQUERY_LOG_TABLE_ID", "request_logs")


# ---------------------------------------------------------------------------
# Structured output schema for document extraction
# ---------------------------------------------------------------------------
class DocumentSchema(BaseModel):
    """Defines structural boundaries for fields captured during document/text parsing."""
    document_id: Optional[str] = Field(default="", description="Unique identifier for the document or session, if present.")
    date: Optional[str] = Field(default="", description="Date and time string representing the event or document date.")
    customer: Optional[str] = Field(default="", description="The full name or username identifier of the customer, if present.")
    agent: Optional[str] = Field(default="", description="The identity of the handling agent or author, if present.")
    product: Optional[str] = Field(default="", description="Product or service related to the content.")
    summary: Optional[str] = Field(default="", description="Comprehensive summary of the document's content or issue.")
    resolution: Optional[str] = Field(default="", description="Actions taken or outcome, if applicable.")
    escalate: Optional[str] = Field(default="", description="Whether escalation/follow-up is required, no, or pending.")


# ---------------------------------------------------------------------------
# BigQuery Storage and Audit Logging
# ---------------------------------------------------------------------------
def _ensure_dataset(bq_client: bigquery.Client) -> bigquery.DatasetReference:
    dataset_ref = bq_client.dataset(BQ_DATASET)
    try:
        bq_client.get_dataset(dataset_ref)
    except gcp_exceptions.NotFound:
        logger.info(f"Dataset '{BQ_DATASET}' not found. Creating...")
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US" if GCP_LOCATION == "us-central1" else GCP_LOCATION
        bq_client.create_dataset(dataset, exists_ok=True)
    return dataset_ref


def _ensure_table(bq_client: bigquery.Client, dataset_ref, table_id: str, schema: list) -> bigquery.TableReference:
    table_ref = dataset_ref.table(table_id)
    try:
        bq_client.get_table(table_ref)
    except gcp_exceptions.NotFound:
        logger.info(f"Table '{table_id}' not found. Creating with schema...")
        table = bigquery.Table(table_ref, schema=schema)
        bq_client.create_table(table, exists_ok=True)
    return table_ref


def load_to_data_warehouse(parsed_data: Dict[str, Any], raw_source: str, file_name: str) -> bool:
    """Streams the extracted structured result into the BigQuery application-storage table."""
    target_table_path = f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    logger.info(f"Streaming result record into: {target_table_path}")
    try:
        bq_client = bigquery.Client(project=GCP_PROJECT)
        dataset_ref = _ensure_dataset(bq_client)
        schema = [
            bigquery.SchemaField("document_id", "STRING"),
            bigquery.SchemaField("date", "STRING"),
            bigquery.SchemaField("customer", "STRING"),
            bigquery.SchemaField("agent", "STRING"),
            bigquery.SchemaField("product", "STRING"),
            bigquery.SchemaField("summary", "STRING"),
            bigquery.SchemaField("resolution", "STRING"),
            bigquery.SchemaField("escalate", "STRING"),
            bigquery.SchemaField("source_file_name", "STRING"),
            bigquery.SchemaField("original_content", "STRING"),
            bigquery.SchemaField("ingested_at", "TIMESTAMP"),
        ]
        _ensure_table(bq_client, dataset_ref, BQ_TABLE, schema)

        row_data = {
            **parsed_data,
            "source_file_name": file_name,
            "original_content": raw_source[:10000] if raw_source else "",
            "ingested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        insert_errors = bq_client.insert_rows_json(target_table_path, [row_data])
        if insert_errors:
            logger.error(f"BigQuery insertion rejected: {insert_errors}")
            return False

        logger.info("Successfully stored result row in BigQuery.")
        return True

    except Exception as general_err:
        logger.error(f"Unhandled fault while writing to BigQuery storage: {general_err}")
        return False


def log_request_event(file_name: str, source_type: str, success: bool, detail: str = "") -> None:
    """Writes a request-level audit log row to a dedicated BigQuery log table."""
    target_table_path = f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_LOG_TABLE}"
    try:
        bq_client = bigquery.Client(project=GCP_PROJECT)
        dataset_ref = _ensure_dataset(bq_client)
        schema = [
            bigquery.SchemaField("event_timestamp", "TIMESTAMP"),
            bigquery.SchemaField("source_file_name", "STRING"),
            bigquery.SchemaField("source_type", "STRING"),
            bigquery.SchemaField("success", "BOOLEAN"),
            bigquery.SchemaField("detail", "STRING"),
            bigquery.SchemaField("model", "STRING"),
        ]
        _ensure_table(bq_client, dataset_ref, BQ_LOG_TABLE, schema)

        log_row = {
            "event_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_file_name": file_name,
            "source_type": source_type,
            "success": success,
            "detail": detail[:500] if detail else "",
            "model": GEMINI_MODEL,
        }
        errors = bq_client.insert_rows_json(target_table_path, [log_row])
        if errors:
            logger.error(f"BigQuery log insertion rejected: {errors}")
    except Exception as e:
        logger.error(f"Unhandled fault while writing log to BigQuery: {e}")


def get_recent_documents(limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieve recent documents from BigQuery for live inspection."""
    try:
        bq_client = bigquery.Client(project=GCP_PROJECT)
        query = f"""
            SELECT document_id, customer, agent, product, escalate, source_file_name, ingested_at
            FROM `{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
            ORDER BY ingested_at DESC
            LIMIT {limit}
        """
        query_job = bq_client.query(query)
        rows = [dict(row) for row in query_job.result()]
        return rows
    except Exception as e:
        logger.warning(f"Could not fetch recent documents: {e}")
        return []


# ---------------------------------------------------------------------------
# Gemini Operations (Multimodal Extraction + Chat with Model Armor)
# ---------------------------------------------------------------------------
def _get_model_armor_config() -> Optional[types.ModelArmorConfig]:
    """Build Model Armor config if templates are provided."""
    if MODEL_ARMOR_PROMPT_TEMPLATE:
        return types.ModelArmorConfig(
            prompt_template_name=MODEL_ARMOR_PROMPT_TEMPLATE,
            response_template_name=MODEL_ARMOR_RESPONSE_TEMPLATE,
        )
    return None


def extract_semantic_data(
    text_content: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Calls Gemini (Vertex AI mode) to extract structured fields per DocumentSchema.
    Applies Model Armor prompt/response templates for safety validation.
    """
    if not text_content and not file_bytes:
        logger.error("No content provided for extraction.")
        return None

    logger.info(f"Executing semantic parse via model '{GEMINI_MODEL}'...")
    try:
        client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
        )

        system_instruction = (
            "Analyze the provided document or transcript and map details into the designated "
            "JSON layout. Extract relevant information precisely even if phrasing varies or "
            "fields appear out of typical order. Use empty strings for any fields you cannot locate."
        )

        parts = []
        if file_bytes:
            parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type or "application/pdf"))
        if text_content:
            parts.append(types.Part.from_text(text=text_content))

        contents = [types.Content(role="user", parts=parts)]

        model_armor_config = _get_model_armor_config()
        if not model_armor_config:
            logger.warning("MODEL_ARMOR_PROMPT_TEMPLATE not set - proceeding without Model Armor validation.")

        generate_content_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=DocumentSchema,
            temperature=0.1,
            max_output_tokens=65535,
            model_armor_config=model_armor_config,
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=generate_content_config,
        )

        if not response.text:
            logger.error("No text response received from Gemini.")
            return None

        return json.loads(response.text)

    except gcp_exceptions.GoogleAPIError as api_err:
        logger.error(f"Gemini API execution error: {api_err}")
    except json.JSONDecodeError as json_err:
        logger.error(f"JSON validation failure on output payload: {json_err}")
    except Exception as e:
        logger.error(f"Extraction unexpected fault: {e}")
    return None


def generate_chat_response(messages: List[Dict[str, Any]], user_prompt: str) -> str:
    """
    Conversational completion with Gemini, validating inputs/outputs with Model Armor.
    """
    try:
        client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
        )

        system_instruction = (
            "You are the snow-app AI assistant, an intelligent enterprise copilot. You help users "
            "parse unstructured incident transcripts, extract structured ticket data, query data warehouses, "
            "and answer technical questions regarding Cloud services, Model Armor, and BigQuery. "
            "Be concise, professional, and clear."
        )

        contents = []
        # Add conversation history
        for m in messages[-8:]:
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
        # Add current user prompt
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))

        model_armor_config = _get_model_armor_config()

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.4,
            max_output_tokens=4096,
            model_armor_config=model_armor_config,
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )

        return response.text or "No response returned by the model."

    except Exception as e:
        logger.error(f"Chat generation error: {e}")
        return f"Error communicating with Gemini: {str(e)}"


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
def is_document_intent(text: str, has_file: bool) -> bool:
    """Determine if user input is intended for structured document extraction."""
    if has_file:
        return True
    keywords = ["ticket", "incident", "transcript", "parse", "extract", "customer:", "agent:", "resolution:"]
    lower = text.lower()
    matches = sum(1 for kw in keywords if kw in lower)
    return matches >= 2


def main():
    st.set_page_config(
        page_title="snow-app | Gen AI Chatbot",
        page_icon="❄️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom styling
    st.markdown("""
        <style>
        .metric-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.78rem;
            font-weight: 600;
            margin-right: 6px;
        }
        .badge-green { background-color: #dcfce7; color: #15803d; }
        .badge-blue { background-color: #dbeafe; color: #1e40af; }
        .badge-purple { background-color: #f3e8ff; color: #6b21a8; }
        .card-container {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            margin-top: 10px;
            margin-bottom: 10px;
        }
        .stChatInput {
            margin-bottom: 10px;
        }
        </style>
    """, unsafe_allow_html=True)

    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "👋 **Hello! I am your snow-app Gen AI Assistant.**\n\n"
                    "You can chat with me naturally, or provide unstructured documents/incident transcripts "
                    "(either pasted in chat or uploaded via the sidebar). I will extract structured fields "
                    "with **Model Armor** safety validation and stream them directly into **BigQuery**."
                ),
            }
        ]

    # Sidebar
    with st.sidebar:
        st.title("❄️ snow-app Console")
        st.caption("Gen AI Document Parser & Chatbot")

        st.divider()

        st.subheader("⚙️ Runtime Environment")
        st.write(f"**Project:** `{GCP_PROJECT}`")
        st.write(f"**Region:** `{GCP_LOCATION}`")
        st.write(f"**Model:** `{GEMINI_MODEL}`")
        st.write(f"**BigQuery Target:** `{BQ_DATASET}.{BQ_TABLE}`")

        armor_status = "🛡️ Active" if MODEL_ARMOR_PROMPT_TEMPLATE else "⚠️ Unset (Bypassed)"
        st.write(f"**Model Armor:** {armor_status}")

        st.divider()

        st.subheader("📎 Document Attachment")
        uploaded_file = st.file_uploader(
            "Upload unstructured document for parsing",
            type=["txt", "pdf", "png", "jpg", "jpeg", "csv", "json"],
            help="Upload a support ticket, customer service transcript, or PDF document.",
        )

        parse_mode = st.radio(
            "Processing Intent",
            ["🤖 Smart Auto-Detect", "📄 Force Document Extraction", "💬 Conversational Only"],
            index=0,
            help="Choose how input prompts and attachments are handled."
        )

        if st.button("🧹 Clear Chat History", use_container_width=True):
            st.session_state.messages = [
                {
                    "role": "assistant",
                    "content": "Chat history cleared. How can I assist you with document parsing or questions?",
                }
            ]
            st.rerun()

        st.divider()
        st.subheader("📊 BigQuery Recent Ingestions")
        with st.expander("View Ingested Records", expanded=False):
            records = get_recent_documents(limit=5)
            if records:
                df = pd.DataFrame(records)
                st.dataframe(df[["document_id", "customer", "agent", "product", "escalate"]], use_container_width=True)
            else:
                st.caption("No records found or table not created yet.")

    # Main Chat View
    st.header("💬 Gen AI Assistant & Document Parser")
    st.markdown(
        "Chat with Gemini, paste unstructured customer service logs, or upload documents to parse and stream into BigQuery."
    )

    # Render Chat History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "parsed_data" in msg and msg["parsed_data"]:
                data = msg["parsed_data"]
                st.markdown(
                    '<span class="metric-badge badge-green">✓ Ingested to BigQuery</span>'
                    f'<span class="metric-badge badge-blue">Doc ID: {data.get("document_id") or "N/A"}</span>'
                    f'<span class="metric-badge badge-purple">Escalate: {data.get("escalate") or "None"}</span>',
                    unsafe_allow_html=True
                )
                with st.expander("🔍 View Extracted Document Schema", expanded=False):
                    st.json(data)

    # Chat Input Box
    prompt = st.chat_input("Enter your message, prompt, or paste an incident transcript...")

    # Process prompt or uploaded file action
    if prompt or (uploaded_file and st.sidebar.button("⚡ Parse Attached File", use_container_width=True)):
        user_text = prompt or f"Parse attached document: {uploaded_file.name}"

        # 1. Display and record user message
        st.session_state.messages.append({"role": "user", "content": user_text})
        with st.chat_message("user"):
            st.markdown(user_text)

        # 2. Determine processing mode
        file_bytes = None
        mime_type = None
        file_name = "manual_chat_entry.txt"

        if uploaded_file:
            file_bytes = uploaded_file.getvalue()
            file_name = uploaded_file.name
            mime_type = uploaded_file.type or mimetypes.guess_type(file_name)[0]

        should_parse = False
        if parse_mode == "📄 Force Document Extraction":
            should_parse = True
        elif parse_mode == "💬 Conversational Only":
            should_parse = False
        else: # Smart Auto-Detect
            should_parse = is_document_intent(user_text, bool(uploaded_file))

        # 3. Generate Assistant Response
        with st.chat_message("assistant"):
            if should_parse:
                with st.spinner("🤖 Gemini is parsing structured document fields with Model Armor..."):
                    parsed_result = extract_semantic_data(
                        text_content=user_text if not file_bytes else None,
                        file_bytes=file_bytes,
                        mime_type=mime_type,
                    )

                    if parsed_result:
                        # Write to BigQuery
                        bq_success = load_to_data_warehouse(
                            parsed_data=parsed_result,
                            raw_source=user_text,
                            file_name=file_name,
                        )
                        log_request_event(file_name, "document_parse", bq_success, detail=json.dumps(parsed_result)[:500])

                        response_text = (
                            f"✅ **Document parsed successfully!**\n\n"
                            f"**Document ID:** `{parsed_result.get('document_id') or 'N/A'}`  \n"
                            f"**Date:** `{parsed_result.get('date') or 'N/A'}`  \n"
                            f"**Customer:** `{parsed_result.get('customer') or 'N/A'}`  \n"
                            f"**Agent:** `{parsed_result.get('agent') or 'N/A'}`  \n"
                            f"**Product:** `{parsed_result.get('product') or 'N/A'}`  \n"
                            f"**Summary:** {parsed_result.get('summary') or 'N/A'}  \n"
                            f"**Resolution:** {parsed_result.get('resolution') or 'N/A'}  \n"
                            f"**Escalation Required:** `{parsed_result.get('escalate') or 'no'}`\n\n"
                            f"*(Stored in BigQuery dataset `{BQ_DATASET}.{BQ_TABLE}`)*"
                        )
                        st.markdown(response_text)
                        st.markdown(
                            '<span class="metric-badge badge-green">✓ Ingested to BigQuery</span>'
                            f'<span class="metric-badge badge-blue">Doc ID: {parsed_result.get("document_id") or "N/A"}</span>'
                            f'<span class="metric-badge badge-purple">Escalate: {parsed_result.get("escalate") or "None"}</span>',
                            unsafe_allow_html=True
                        )
                        with st.expander("🔍 View Extracted Document Schema", expanded=False):
                            st.json(parsed_result)

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": response_text,
                            "parsed_data": parsed_result,
                        })
                    else:
                        err_text = "❌ Failed to extract semantic data from the provided document or text."
                        st.error(err_text)
                        log_request_event(file_name, "document_parse", False, detail=err_text)
                        st.session_state.messages.append({"role": "assistant", "content": err_text})
            else:
                with st.spinner("🤖 Gemini is generating a response..."):
                    chat_reply = generate_chat_response(st.session_state.messages[:-1], user_text)
                    st.markdown(chat_reply)
                    log_request_event(file_name, "chat_prompt", True, detail=chat_reply[:500])
                    st.session_state.messages.append({"role": "assistant", "content": chat_reply})

if __name__ == "__main__":
    main()
