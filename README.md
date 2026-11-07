# Amafah - WhatsApp Purchase Manager AI Agent

An intelligent, autonomous AI agent webhook service built with **FastAPI**, **OpenClaw**, and **Supabase (PostgreSQL)** for processing incoming supplier RFQ (Request for Quotation) responses on WhatsApp through the OpenClaw gateway.

## Features

- **Autonomous Tool Calling**: OpenClaw interprets supplier messages and calls authenticated procurement tools; the backend validates commercial authority before mutations and commitments.
- **WhatsApp Webhook Integration**: OpenClaw owns supplier conversations and transport. The backend exposes the `/openclaw` tool bridge.
- **RFQ Tracking & Management**: Automatically matches supplier quotes, specs, delivery lead times, and status against open RFQs.
- **Supabase Integration**: Direct database interactions for managing suppliers, products, and RFQs.

## Project Structure

```
.
├── main.py            # Buyer APIs, scheduling, dispatch and reports
├── openclaw_bridge.py  # Procurement tools, analysis and commercial authorization
├── db.py              # Supabase database layer & query functions
├── schema.sql         # PostgreSQL schema definition
├── requirements.txt   # Python dependencies
└── .env.example       # Example environment variables setup
```

## Getting Started

### 1. Prerequisites

- Python 3.9+
- OpenClaw service configured with a model
- OPENCLAW_BASE_URL and shared OPENCLAW_TOOL_API_KEY
- Supabase project credentials

### 2. Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/noobpogrammer/amafah-purchase-manager-ai-agent.git
   cd amafah-purchase-manager-ai-agent
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables:
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

### 3. Running the Application

Start the FastAPI server:
```bash
uvicorn main:app --reload
```

### 4. Database Schema & Migrations

> **CRITICAL RULE:** Never edit `schema.sql` directly as the source of truth. All schema changes MUST go through `supabase migration new <descriptive_name>` + `supabase db push`. After creating a migration, verify it applied successfully by querying the live schema before considering the task done.

To create and apply a schema change:
1. Create a new migration file:
   ```bash
   supabase migration new <descriptive_name>
   ```
2. Put the SQL changes in `supabase/migrations/<timestamp>_<descriptive_name>.sql`.
3. Apply to live database:
   ```bash
   supabase db push
   ```

## License

MIT


## OpenClaw-only migration

The legacy action validator, local AI reasoning/tool loop, text guardrails and Groq client have been removed. Commercial action review remains in `openclaw_bridge.py`; its approval and acceptance RPCs remain authoritative.

Three analysis functions call the authenticated `POST /internal/procurement/analyze` endpoint in the companion `openclaw-railway-template` service: commercial fact/tradeoff extraction, multi-item quote parsing and quote ranking. Sessions are isolated and the procurement plugin disables tools for these analysis sessions. No WhatsApp delivery is requested. Returned facts are checked by backend schemas and existing RFQ/quote checks. Commercial parsing retains the existing deterministic fallback; multi-item parsing returns no mapped items on failure; ranking errors propagate without saving an invented recommendation.

Deploy the updated OpenClaw wrapper **and procurement-tools plugin first**, then this backend. No database migrations are required. A backend deployed against the old wrapper cannot use the new analysis endpoint; parsing falls back and ranking fails. No Groq credentials or Groq SDK are required.

Local verification covers analysis transport contracts, parsing failures, candidate validation, rankings, existing bridge flows and wrapper response extraction. Live gateway/model execution must still be checked against the deployed OpenClaw service.
