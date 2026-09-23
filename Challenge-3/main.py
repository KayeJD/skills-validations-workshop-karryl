import os
import json
import tempfile
from flask import Flask, request, render_template_string, redirect
from google.cloud import storage
import parse_transcript

app = Flask(__name__)
storage_client = storage.Client()

HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Transcript Parser UI</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-50 font-sans text-gray-800">
        <div class="max-w-4xl mx-auto py-10 px-4">
            <header class="mb-8">
                <h1 class="text-3xl font-bold text-gray-900">Customer Service Transcript Parser</h1>
                <p class="text-gray-600 mt-2">Paste a customer support log transcript below to extract structured entities semantically and stream them directly to BigQuery.</p>
            </header>

            <div class="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
                <form method="POST" action="/ui">
                    <div class="mb-4">
                        <label for="transcript" class="block text-sm font-semibold text-gray-700 mb-2">Transcript Content</label>
                        <textarea id="transcript" name="transcript" rows="12" class="w-full p-3 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm" placeholder="Support Session Reference: INC-44912..." required></textarea>
                    </div>
                    <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-4 rounded-md transition duration-200 cursor-pointer">
                        Analyze & Ingest Transcript
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

@app.route("/", methods=["GET", "POST"])
def handle_eventarc_notification():
    if request.method == "GET":
        return redirect("/ui")
        
    event_data = request.get_json()
    if not event_data:
        return ("Bad Request: Missing JSON payload", 400)

    # Parse CloudEvent or Storage notification metadata
    bucket_name = event_data.get("bucket")
    file_name = event_data.get("name")

    if not bucket_name or not file_name:
        return ("Ignored non-GCS finalize event", 200)

    print(f"Processing event for bucket: {bucket_name}, object: {file_name}")

    try:
        # Download object from Cloud Storage into a temporary local file
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp_file:
            blob.download_to_filename(tmp_file.name)
            local_path = tmp_file.name

        # Execute semantic extraction script
        with open(local_path, "r", encoding="utf-8") as f:
            content = f.read()

        parsed_data = parse_transcript.extract_semantic_data(content)
        if parsed_data:
            success = parse_transcript.load_to_data_warehouse(
                parsed_data=parsed_data,
                raw_transcript=content,
                file_name=file_name
            )
            if success:
                return ("Successfully processed and loaded transcript", 200)

        return ("Failed processing semantic data", 500)

    except Exception as exc:
        print(f"Unhandled pipeline error: {exc}")
        return (f"Error: {str(exc)}", 500)
    finally:
        if 'local_path' in locals() and os.path.exists(local_path):
            os.remove(local_path)

@app.route("/ui", methods=["GET", "POST"])
def ui_interface():
    result = None
    success = False
    if request.method == "POST":
        content = request.form.get("transcript", "").strip()
        if content:
            parsed_data = parse_transcript.extract_semantic_data(content)
            if parsed_data:
                success = parse_transcript.load_to_data_warehouse(
                    parsed_data=parsed_data,
                    raw_transcript=content,
                    file_name="manual_ui_entry.txt"
                )
                result = json.dumps(parsed_data, indent=2, ensure_ascii=False)
            else:
                result = "Failed to extract semantic data from the transcript."
    return render_template_string(HTML_TEMPLATE, result=result, success=success)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)