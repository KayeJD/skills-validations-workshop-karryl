#!/usr/bin/env python3
"""
snow-app.py
===========
Sample Generative AI application skeleton built on the Gemini API (Vertex AI mode).

Setup & environment
--------------------
Requirements: pip install google-genai google-cloud-bigquery google-cloud-storage pydantic flask gunicorn
Authentication: Google Application Default Credentials (ADC) - via `gcloud auth application-default login`
locally, or the Cloud Run service account in production since keys are not available

Required env vars:
  GOOGLE_CLOUD_PROJECT          GCP project id
  GCP_LOCATION                  Vertex AI region, e.g. us-central1 (default: us-central1)
  GEMINI_MODEL                  Model id, e.g. gemini-2.5-flash (default set below)
  MODEL_ARMOR_PROMPT_TEMPLATE   Full resource name of the Model Armor prompt template
  MODEL_ARMOR_RESPONSE_TEMPLATE Full resource name of the Model Armor response template
                                 (projects/PROJECT/locations/LOCATION/templates/TEMPLATE_ID)
  BIGQUERY_DATASET_ID           BigQuery dataset (default: snow_app)
  BIGQUERY_TABLE_ID             BigQuery results table (default: documents)
  BIGQUERY_LOG_TABLE_ID         BigQuery log table (default: request_logs)
  PORT                          Cloud Run injects this automatically (default: 8080)
"""

import os
import sys
import json
import logging
import datetime
import tempfile
import mimetypes
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field
from flask import Flask, request, render_template_string, redirect

try:
    from google import genai
    from google.genai import types
    from google.cloud import bigquery
    from google.cloud import storage
    from google.api_core import exceptions as gcp_exceptions
except ImportError as e:
    print(f"Missing dependencies: {e}. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("snow-app")

# ---------------------------------------------------------------------------
# Configuration (env-driven, Cloud Run friendly)
# ---------------------------------------------------------------------------
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Model Armor templates
MODEL_ARMOR_PROMPT_TEMPLATE = os.environ.get("MODEL_ARMOR_PROMPT_TEMPLATE", "")
MODEL_ARMOR_RESPONSE_TEMPLATE = os.environ.get("MODEL_ARMOR_RESPONSE_TEMPLATE", MODEL_ARMOR_PROMPT_TEMPLATE)

BQ_DATASET = os.environ.get("BIGQUERY_DATASET_ID", "snow_app")
BQ_TABLE = os.environ.get("BIGQUERY_TABLE_ID", "documents")
BQ_LOG_TABLE = os.environ.get("BIGQUERY_LOG_TABLE_ID", "request_logs")

app = Flask(__name__)
storage_client = storage.Client()


# ---------------------------------------------------------------------------
# Structured output schema
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
#  Gemini extraction with Model Armor prompt/response validation
#  Supports plain text OR multimodal file bytes (PDF, image, etc.)
# ---------------------------------------------------------------------------
def extract_semantic_data(
    text_content: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Calls Gemini (Vertex AI mode) to extract structured fields per DocumentSchema.
    Applies Model Armor prompt/response templates for safety validation.
    Accepts either raw text or an uploaded document's bytes (multimodal).
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

        # Build multimodal content: file bytes take precedence, falling back to plain text.
        parts = []
        if file_bytes:
            parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type or "application/pdf"))
        if text_content:
            parts.append(types.Part.from_text(text=text_content))

        contents = [types.Content(role="user", parts=parts)]

        # Model Armor: screens both the outbound prompt and the inbound model response
        # against a pre-configured template (created in Google Cloud, not via this SDK).
        model_armor_config = None
        if MODEL_ARMOR_PROMPT_TEMPLATE:
            model_armor_config = types.ModelArmorConfig(
                prompt_template_name=MODEL_ARMOR_PROMPT_TEMPLATE,
                response_template_name=MODEL_ARMOR_RESPONSE_TEMPLATE,
            )
        else:
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


# ---------------------------------------------------------------------------
# BigQuery: application storage (results) and logging (every request)
# ---------------------------------------------------------------------------
def _ensure_dataset(bq_client: bigquery.Client) -> bigquery.DatasetReference:
    dataset_ref = bq_client.dataset(BQ_DATASET)
    try:
        bq_client.get_dataset(dataset_ref)
    except gcp_exceptions.NotFound:
        logger.info(f"Dataset '{BQ_DATASET}' not found. Creating...")
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = GCP_LOCATION
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
            "original_content": raw_source,
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
    """
    Writes a request-level audit/log row to a dedicated BigQuery log table, independent of
    whether extraction/storage succeeded. This is the 'logging to BigQuery' requirement,
    kept separate from the application data table above.
    """
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
            "detail": detail,
            "model": GEMINI_MODEL,
        }
        errors = bq_client.insert_rows_json(target_table_path, [log_row])
        if errors:
            logger.error(f"BigQuery log insertion rejected: {errors}")
    except Exception as e:
        logger.error(f"Unhandled fault while writing log to BigQuery: {e}")


# ---------------------------------------------------------------------------
# Web UI (Flask + Tailwind, paste text or upload a file)
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>snow-app | Gen AI Document Parser</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-50 font-sans text-gray-800">
        <div class="max-w-4xl mx-auto py-10 px-4">
            <header class="mb-8">
                <h1 class="text-3xl font-bold text-gray-900">snow-app: Gen AI Document Parser</h1>
                <p class="text-gray-600 mt-2">Paste text or upload a document (PDF, image, txt). Gemini extracts structured fields
                (with Model Armor validation) and streams the result to BigQuery.</p>
            </header>

            <div class="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                <form method="POST" action="/ui" enctype="multipart/form-data">
                    <div class="mb-4">
                        <label for="transcript" class="block text-sm font-semibold text-gray-700 mb-2">Paste Text</label>
                        <textarea id="transcript" name="transcript" rows="10" class="w-full p-3 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm" placeholder="Paste transcript or document text here..."></textarea>
                    </div>
                    <div class="mb-4">
                        <label for="document" class="block text-sm font-semibold text-gray-700 mb-2">...or upload a file (PDF / image / txt)</label>
                        <input type="file" id="document" name="document" class="w-full text-sm border border-gray-300 rounded-md p-2">
                    </div>
                    <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-4 rounded-md transition duration-200 cursor-pointer">
                        Analyze & Ingest
                    </button>
                </form>
            </div>

            {% if result %}
            <div class="mt-8 bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                <h2 class="text-xl font-bold text-gray-900 mb-4">Parsing Results</h2>
                <div class="mb-4">
                    <span class="px-2.5 py-1 text-xs font-semibold rounded-full {% if success %}bg-green-100 text-green-800{% else %}bg-red-100 text-red-800{% endif %}">
                        {% if success %}Successfully Ingested to BigQuery{% else %}Extraction Failed or Warehouse Ingestion Blocked{% endif %}
                    </span>
                </div>
                <pre class="bg-gray-900 text-green-400 p-4 rounded-md overflow-x-auto text-sm font-mono"><code>{{ result }}</code></pre>
            </div>
            {% endif %}
        </div>
    </body>
    </html>
"""


@app.route("/ui", methods=["GET", "POST"])
def ui_interface():
    result = None
    success = False
    if request.method == "POST":
        text_content = request.form.get("transcript", "").strip()
        uploaded_file = request.files.get("document")

        file_bytes, mime_type, file_name = None, None, "manual_ui_entry.txt"
        if uploaded_file and uploaded_file.filename:
            file_bytes = uploaded_file.read()
            mime_type = uploaded_file.mimetype or mimetypes.guess_type(uploaded_file.filename)[0]
            file_name = uploaded_file.filename

        if text_content or file_bytes:
            parsed_data = extract_semantic_data(text_content=text_content or None, file_bytes=file_bytes, mime_type=mime_type)
            source_type = "file" if file_bytes else "text"
            if parsed_data:
                success = load_to_data_warehouse(
                    parsed_data=parsed_data,
                    raw_source=text_content or f"<binary file: {file_name}>",
                    file_name=file_name,
                )
                result = json.dumps(parsed_data, indent=2, ensure_ascii=False)
            else:
                result = "Failed to extract semantic data from the input."
            log_request_event(file_name, source_type, success, detail=result[:500] if result else "")
    return render_template_string(HTML_TEMPLATE, result=result, success=success)


@app.route("/", methods=["GET", "POST"])
def handle_eventarc_notification():
    if request.method == "GET":
        return redirect("/ui")

    event_data = request.get_json(silent=True)
    if not event_data:
        return ("Bad Request: Missing JSON payload", 400)

    bucket_name = event_data.get("bucket")
    file_name = event_data.get("name")
    if not bucket_name or not file_name:
        return ("Ignored non-GCS finalize event", 200)

    logger.info(f"Processing event for bucket: {bucket_name}, object: {file_name}")
    local_path = None
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        suffix = os.path.splitext(file_name)[1] or ".bin"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            blob.download_to_filename(tmp_file.name)
            local_path = tmp_file.name

        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            raw_bytes = f.read()

        # Try to decode as text first (e.g. .txt transcripts); otherwise treat as a
        # multimodal document (PDF/image) for Gemini to parse directly.
        text_content = None
        file_bytes = None
        try:
            text_content = raw_bytes.decode("utf-8")
            file_bytes = None
        except UnicodeDecodeError:
            file_bytes = raw_bytes

        parsed_data = extract_semantic_data(text_content=text_content, file_bytes=file_bytes, mime_type=mime_type)
        success = False
        if parsed_data:
            success = load_to_data_warehouse(
                parsed_data=parsed_data,
                raw_source=text_content or f"<binary file: {file_name}>",
                file_name=file_name,
            )
        log_request_event(file_name, "gcs_event", success)

        if success:
            return ("Successfully processed and loaded document", 200)
        return ("Failed processing semantic data", 500)

    except Exception as exc:
        logger.error(f"Unhandled pipeline error: {exc}")
        log_request_event(file_name, "gcs_event", False, detail=str(exc))
        return (f"Error: {str(exc)}", 500)
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
