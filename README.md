# Challenge 1: AI Security and SensitiveData Protection
Goal: Build a secure Gemini chat app that answers user questions and has access to Google Search.
Do this in a Jupyter notebook or by creating a simple Python app.

Configure Gemini API safety settings, utilize Google Cloud Model Armor to evaluate user inputs for
prompt injections, and apply the Sensitive Data Protection API to scan model outputs for sensitive data
leaks.

Requirements:
- Using the Gemini API, create function that uses Gemini to respond to user queries
- Configure Safety Settings and appropriate instructions
- Configure the Google Search tool
- Bonus: Use Model Armor to moderate user prompts and model responses

# Challenge 2: Code Refactoring with Gemini Code Assist
Goal: In Google Cloud Shell, use Gemini Code Assist, the Gemini CLI, and/or the Antigravity CLI to
analyze brittle legacy regular expression scripts and rewrite them as modern Python functions.

These new functions must use the Gemini API to semantically extract data from unstructured text into structured JSON, while adding automated error handling and generating technical documentation. The extracted data will be logged and saved to BigQuery.

Requirements:
- Rewrite the legacy shell script using Python
- Parse customer service transcripts with the help of the Gemini API
- Write results to BigQuery (Parsed transcript, original transcript, log history)
- Create a simple web-based UI for users

# Challenge 3: Architecting with Gemini Cloud Assist
Goal: Use the Gemini Cloud Assist to architect a secure deployment for your data-extraction program
from the last lab. Prompt Gemini to generate the necessary Terraform code and build a deployment
pipeline to securely deploy the code to Google Cloud.

Ensure the architecture meets the strict data isolation requirements. Create all necessary infrastructure using Terraform and automates scripts.

Requirements:
- Create an architectural diagram that depicts how the application will be deployed
- Write Terraform code for deploying resources
- Automate the deployment

```mermaid
graph TD
    subgraph External_World
        User[Customer Support User]
    end

    subgraph GCP_Project_Perimeter [VPC Service Controls Perimeter]
        subgraph Compute_Layer
            CloudRun[Cloud Run: Flask App]
            SA[Service Account: CR-Identity]
        end

        subgraph Storage_Layer
            GCS[GCS: Input Documents]
            BQ[BigQuery: Parsed Data]
        end

        subgraph ML_Layer
            VertexAI[Vertex AI / Gemini API]
        end

        subgraph Networking
            VPC[Virtual Private Cloud]
            VPCConnector[VPC Access Connector]
        end
    end

    subgraph CI_CD_Pipeline
        CB[Cloud Build]
        AR[Artifact Registry]
    end

    User -->|HTTPS| CloudRun
    CloudRun -->|VPC Connector| VPC
    VPC -->|Private Google Access| GCS
    VPC -->|Private Google Access| BQ
    VPC -->|Private Google Access| VertexAI
    
    CB -->|Build & Push| AR
    AR -->|Deploy Image| CloudRun
    SA -->|IAM: Least Privilege| GCS
    SA -->|IAM: Least Privilege| BQ
    SA -->|IAM: Least Privilege| VertexAI

```