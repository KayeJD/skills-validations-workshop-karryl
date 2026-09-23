#!/usr/bin/env python3
"""
Customer Service Transcript Parser
=================================
This utility modernizes legacy regex-based extraction scripts. It uses the modern 
Google Gen AI SDK (Gemini API) to perform semantic data extraction from unstructured 
or clean support logs, providing structured JSON output and storing data into BigQuery.

Technical Documentation
-----------------------
1. Core Architecture:
   - Semantic Extraction: Utilizing Gemini models combined with structured output schemas 
     (Pydantic validation) to guarantee JSON uniformity regardless of source line order or format.
   - Resilient Storage: Concurrently preserves a local JSON data artifact and streams data records 
     directly into the analytical warehouse (BigQuery).

2. Capabilities & Error Handling:
   - Full tolerance for format drifts, conversational shifts, and missing attributes.
   - Robust exception containment capturing file system faults, API communication failure, 
     and downstream data warehouse ingestion obstacles.

3. Setup & Environment:
   - Requirements: `pip install google-genai google-cloud-bigquery pydantic`
   - Authentication: Relies automatically on Google Application Default Credentials (ADC).
"""

import os
import sys
import json
import logging
import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
    import google.cloud.aiplatform as aiplatform # Added for Vertex AI initialization
    from google.cloud import bigquery 
    from google.api_core import exceptions as gcp_exceptions
except ImportError as e:
    print(f"Missing dependencies: {e}. Please run: pip install google-genai google-cloud-bigquery pydantic google-cloud-aiplatform", file=sys.stderr)
    sys.exit(1)

# Initialize structured logging infrastructure
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TranscriptParser")

# Configuration managed via environmental contexts with fallback values
GEMINI_MODEL = "gemini-2.5-flash"
BQ_PROJECT = os.environ.get("BIGQUERY_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET_ID", "customer_service_parser")
GCP_LOCATION = "us-central1" # Default location for Vertex AI
BQ_TABLE = os.environ.get("BIGQUERY_TABLE_ID", "transcripts")

class TranscriptSchema(BaseModel):
    """Defines structural boundaries for fields captured during transcript parsing."""
    call_id: Optional[str] = Field(default="", description="Unique identifier for the customer service session.")
    date: Optional[str] = Field(default="", description="Date and time string representing the event time.")
    customer: Optional[str] = Field(default="", description="The full name or username identifier of the customer.")
    agent: Optional[str] = Field(default="", description="The identity of the handling service agent.")
    product: Optional[str] = Field(default="", description="Product or software service ecosystem related to the problem.")
    issue: Optional[str] = Field(default="", description="Comprehensive summary detailing the reported issue.")
    resolution: Optional[str] = Field(default="", description="Actions taken to remediate the client situation.")
    escalate: Optional[str] = Field(default="", description="Determines whether escalation is marked as required, no, or pending.")

def extract_semantic_data(transcript_content: str) -> Optional[Dict[str, Any]]:
    """
    Communicates with the Gemini API to extract structured fields according to TranscriptSchema.
    """
    logger.info(f"Executing semantic parse analysis via model '{GEMINI_MODEL}'...")
    try:
        client = genai.Client(
            vertexai=True,
            project=BQ_PROJECT,
            location=GCP_LOCATION
        )
        
        system_instruction = (
            "Analyze customer service transcripts and map details into the designated JSON layout. "
            "Extract relevant information precisely even if conversational phrasing changes or "
            "fields appear out of typical order. Use empty strings for any fields you cannot locate."
        )
        
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=transcript_content,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=TranscriptSchema,
                temperature=0.1
            )
        )
        
        if not response.text:
            logger.error("No text response received from GenAI model.")
            return None
            
        return json.loads(response.text)
        
    except gcp_exceptions.GoogleAPIError as api_err:
        logger.error(f"Gemini API execution error: {api_err}")
    except json.JSONDecodeError as json_err:
        logger.error(f"JSON validation failure on output payload: {json_err}")
    except Exception as e:
        logger.error(f"Automated extraction unexpected fault: {e}")
    return None

def load_to_data_warehouse(parsed_data: Dict[str, Any], raw_transcript: str, file_name: str) -> bool:
    """
    Streams record telemetry (parsed entities, raw source body, and operational logs) into BigQuery.
    """
    target_table_path = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    logger.info(f"Streaming data records directly into warehouse table: {target_table_path}")
    
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT)

        # Ensure target BigQuery dataset exists
        dataset_ref = bq_client.dataset(BQ_DATASET)
        try:
            bq_client.get_dataset(dataset_ref)
        except gcp_exceptions.NotFound:
            logger.info(f"Dataset '{BQ_DATASET}' not found. Creating dataset...")
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = GCP_LOCATION
            bq_client.create_dataset(dataset, exists_ok=True)

        # Ensure target BigQuery table exists
        table_ref = dataset_ref.table(BQ_TABLE)
        try:
            bq_client.get_table(table_ref)
        except gcp_exceptions.NotFound:
            logger.info(f"Table '{BQ_TABLE}' not found. Creating table with schema...")
            schema = [
                bigquery.SchemaField("call_id", "STRING"),
                bigquery.SchemaField("date", "STRING"),
                bigquery.SchemaField("customer", "STRING"),
                bigquery.SchemaField("agent", "STRING"),
                bigquery.SchemaField("product", "STRING"),
                bigquery.SchemaField("issue", "STRING"),
                bigquery.SchemaField("resolution", "STRING"),
                bigquery.SchemaField("escalate", "STRING"),
                bigquery.SchemaField("original_transcript", "STRING"),
                bigquery.SchemaField("log_history", "STRING"),
            ]
            table = bigquery.Table(table_ref, schema=schema)
            bq_client.create_table(table, exists_ok=True)
        
        log_history = {
            "execution_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_origin_file": file_name,
            "ingest_engine": "Gemini-Semantic-Parser-v2"
        }
        
        # Assemble data mapping matching requested fields plus context storage
        row_data = {**parsed_data, "original_transcript": raw_transcript, "log_history": json.dumps(log_history)}
        
        insert_errors = bq_client.insert_rows_json(target_table_path, [row_data])
        if insert_errors:
            logger.error(f"BigQuery record insertion rejected with errors: {insert_errors}")
            return False
            
        logger.info("Successfully recorded telemetry row & execution log history to BigQuery.")
        return True
        
    except gcp_exceptions.NotFound:
        logger.error(f"Target dataset or table not found at path '{target_table_path}'. Check your infrastructure configuration.")
    except Exception as general_err:
        logger.error(f"Unhandled fault while communicating with BigQuery: {general_err}")
    return False

def main():
    if len(sys.argv) != 2:
        logger.error("Syntax Error. Usage: python3 parse_transcript.py <transcript_file.txt>")
        sys.exit(1)
        
    source_file = sys.argv[1]
    if not os.path.isfile(source_file):
        logger.error(f"Target document not found on file system: {source_file}")
        sys.exit(1)
        
    with open(source_file, "r", encoding="utf-8") as fs:
        content = fs.read()
        
    parsed_json = extract_semantic_data(content)
    if parsed_json:
        # Output locally into directory mirroring old tool configurations
        os.makedirs("output", exist_ok=True)
        base = os.path.splitext(os.path.basename(source_file))[0]
        out_path = os.path.join("output", f"{base}.json")
        
        with open(out_path, "w", encoding="utf-8") as out_f:
            json.dump(parsed_json, out_f, indent=2, ensure_ascii=False)
        logger.info(f"Local structural fallback artifact saved to {out_path}")
        
        # Send the complete historical matrix to the analytics engine
        load_to_data_warehouse(parsed_json, content, os.path.basename(source_file))
    else:
        logger.error("Processing sequence aborted due to internal parser breakdown.")
        sys.exit(1)

if __name__ == "__main__":
    main()