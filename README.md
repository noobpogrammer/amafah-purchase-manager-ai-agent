# Amafah - WhatsApp Purchase Manager AI Agent

An intelligent, autonomous AI agent webhook service built with **FastAPI**, **Groq (LLM & Tool Calling)**, and **Supabase (PostgreSQL)** for processing incoming supplier RFQ (Request for Quotation) responses on WhatsApp via Evolution API.

## Features

- **Autonomous Tool Calling**: Leverages Groq's LLM tool calling capabilities to interpret supplier messages and execute precise database operations.
- **WhatsApp Webhook Integration**: Processes incoming webhooks from Evolution API to handle real-time communication with suppliers.
- **RFQ Tracking & Management**: Automatically matches supplier quotes, specs, delivery lead times, and status against open RFQs.
- **Supabase Integration**: Direct database interactions for managing suppliers, products, and RFQs.

## Project Structure

```
.
├── main.py            # FastAPI entry point & Evolution API webhook handler
├── groq_client.py     # Groq LLM integration & Tool execution engine
├── db.py              # Supabase database layer & query functions
├── schema.sql         # PostgreSQL schema definition
├── requirements.txt   # Python dependencies
└── .env.example       # Example environment variables setup
```

## Getting Started

### 1. Prerequisites

- Python 3.9+
- Groq API Key
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

