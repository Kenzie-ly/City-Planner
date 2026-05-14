# Documentation for app.py - Infrastructure Planner API

## Overview

This file (`app.py`) is the main backend server for an **Infrastructure Planner** application. It uses FastAPI (a Python web framework) to create an API that helps plan urban infrastructure improvements, especially for transportation and city planning. The app integrates AI agents to analyze data, generate plans, and simulate building projects.

Think of it as a smart assistant that:
- Collects evidence about city needs (like traffic problems, population growth)
- Suggests improvement plans
- Runs simulations of proposed buildings
- Provides a chat interface for users to interact with the planning process

## Key Concepts for Beginners

### What is an API?
An API (Application Programming Interface) is like a waiter in a restaurant. Your frontend app (the "customer") asks the API (waiter) for data or actions, and the API talks to the backend systems (kitchen) to get what you need.

### What are AI Agents?
In this code, "agents" are AI-powered assistants that perform specific tasks. Each agent is like a specialist:
- One agent finds places to improve
- Another analyzes growth signals
- Another plans improvements
- And so on...

### What is FastAPI?
FastAPI is a modern Python framework for building APIs quickly. It's fast, has automatic documentation, and handles data validation automatically.

## Main Components

### 1. FastAPI Application Setup
```python
app = FastAPI(title="Infrastructure Planner API")
```
This creates the main web application. It includes CORS middleware to allow the frontend (running on localhost:3000) to communicate with this backend.

### 2. Environment Variables
The app loads settings from environment variables:
- `GOOGLE_API_KEY`: For accessing Google services
- `GROWTH_FLOW_ENABLED`: Whether to enable growth analysis features
- `ENABLE_SPECULATIVE_FIND_NEEDS`: Whether to allow speculative planning

### 3. AI Agents
The code imports several AI agents from `agent.py`:
- `place_intake_agent`: Analyzes locations
- `find_needs_agent`: Identifies problems that need fixing
- `growth_signal_agent`: Analyzes population and economic growth
- `planning_agent`: Creates improvement plans
- `solution_agent`: Generates specific solutions
- `building_agent`: Simulates building projects
- `review_agent`: Reviews and validates plans

### 4. Evidence Pipeline
Functions from `evidence_pipeline.py` handle data collection and analysis:
- `collect_google_growth_signals`: Gets data from Google about city growth
- `cluster_findings_to_area_options`: Groups data into planning areas
- `audit_osm_transit_gap`: Checks public transit coverage using OpenStreetMap data
- `compute_merged_confidence`: Calculates how reliable the data is

### 5. Workflow Pipeline
The `PIPELINE` list defines the main steps of the planning process:
1. Plan improvements
2. Generate solutions  
3. Building simulations

## Main Functions

### Chat Handling
- `chat()`: Processes user messages and advances through the planning workflow
- Uses session IDs to keep track of conversations
- Calls AI agents based on the current planning phase

### Data Processing Functions
- `clean_json_text()`: Cleans up JSON data from AI responses
- `safe_json_loads()`: Safely parses JSON data
- `parse_place_result()`: Extracts place information from AI responses
- `parse_review()`: Parses review feedback from agents

### Evidence and Planning Functions
- `prepare_find_needs_output()`: Prepares options for what needs to be improved
- `build_find_needs_options()`: Creates challenge options based on evidence
- `_synthesize_area_card_content()`: Creates detailed area descriptions

## How It Works

1. **User starts a session** by sending a message
2. **Place intake**: AI analyzes the location mentioned
3. **Find needs**: AI identifies problems and creates "challenge cards" 
4. **User selects** which challenge to focus on
5. **Planning**: AI creates improvement plans
6. **Solutions**: AI generates specific solutions
7. **Building simulation**: AI simulates the proposed buildings
8. **Review**: AI reviews the final plan

## Running the Application

### Prerequisites
- Python 3.8+
- Required packages (see `requirements.txt`)
- Google API key
- Environment variables set

### Starting the Server
```bash
python app.py
```
The API will be available at `http://localhost:8000`

### API Endpoints
- `POST /start`: Start a new planning session
- `POST /chat`: Send a message in an existing session

## Dependencies

Key Python packages used:
- `fastapi`: Web framework
- `pydantic`: Data validation
- `uvicorn`: ASGI server (usually started separately)
- `google.adk`: Google's AI development kit
- `requests`: HTTP requests
- `python-dotenv`: Environment variable loading

## Error Handling

The app includes retry logic for AI agent calls and fallback mechanisms when data is incomplete. It validates all inputs and provides helpful error messages.

## Security Features

- CORS configuration for frontend communication
- Input validation using Pydantic models
- Session-based conversation tracking
- Evidence filtering and validation

This documentation covers the basics. For more detailed technical information, see the inline comments in the code and the FastAPI automatic documentation at `/docs` when the server is running.