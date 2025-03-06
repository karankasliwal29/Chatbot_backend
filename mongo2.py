# Code which handles multiple collections with keywords such as todays, yesterdays, etc.

from flask import Flask, request, jsonify
import os
import openai
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId
from rapidfuzz import fuzz
from flask_cors import CORS
from datetime import datetime, timedelta
import dateparser
import re

# Load environment variables
load_dotenv()

# MongoDB connection
MONGO_URI = os.getenv("MONGO_CONNECTION_STRING")
DATABASE_NAME = os.getenv("DATABASE_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI and MongoDB
openai.api_key = OPENAI_API_KEY
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

# Flask app initialization
app = Flask(__name__)
CORS(app)  # Enable Cross-Origin Resource Sharing (CORS)

def log(message):
    """Helper function to print debug logs."""
    print(f"🔍 {message}")

def extract_date_from_query(query):
    """Extracts date from query, handling keywords like 'today' and 'yesterday'."""
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    query_lower = query.lower()

    if "today" in query_lower:
        return today
    elif "yesterday" in query_lower:
        return yesterday
    elif "this month" in query_lower:
        return today.replace(day=1)  # Start of the current month.

    date_patterns = [
        r'\b\d{1,2}[st|nd|rd|th]*\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b',
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{1,2}\b',
        r'\b\d{1,2}-\d{1,2}-\d{4}\b'
    ]

    for pattern in date_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            parsed_date = dateparser.parse(match.group(0))
            return parsed_date.date() if parsed_date else None

    return None

def create_date_filter_from_query(query):
    """Creates MongoDB date filter from extracted date."""
    log(f"Extracting date from query: {query}")

    extracted_date = extract_date_from_query(query)
    if not extracted_date:
        log("⚠️ No valid date found in query!")
        return None

    log(f"📅 Using extracted date: {extracted_date}")

    start_of_day = datetime(extracted_date.year, extracted_date.month, extracted_date.day, 0, 0, 0)
    end_of_day = start_of_day + timedelta(days=1)

    return {
        "$or": [
            {"updatedAt": {"$gte": start_of_day, "$lt": end_of_day}},
            {"startTimestamp": {"$gte": start_of_day, "$lt": end_of_day}},
            {"breakdownStartDateTime": {"$gte": start_of_day, "$lt": end_of_day}},
            {"CreatedAt": {"$gte": start_of_day, "$lt": end_of_day}}
        ]
    }

def identify_collections(query):
    """Identify multiple collections relevant to the user query."""
    query_lower = query.lower().strip()
    log(f"Identifying collections for query: {query_lower}")

    collection_mapping = {
        "alarm": "alarmHistory",
        "oee": "oeelog1",
        "downtime": "downtimes",
        "maintenance": "maintenanceschedules",
        "alert": "alerts",
        "total parts are produced": "oeelog1",
        "production data for CN15": "ORG001_CN15_productionData",
        "production data for CN14": "ORG001_CN14_productionData",
        "quality": "oeelog1",
        "availability": "oeelog1",
        "performance": "oeelog1",
        "task": "maintenanceschedules",
        "parameter": "pmc_parameters",
        "bit position": "pmc_parameters",
        "tool": "tooldetails",
        "set life": "tooldetails",
        "threshold": "diagnostics",
        "planned quantity": "oeelog1",
        "defective parts": "oeelog1",
        "downtime duration": "oeelog1",
        "cycle time": "oeelog1"
        }

    matched_collections = [
        coll_name for keyword, coll_name in collection_mapping.items() if keyword in query_lower
    ]

    # Fuzzy match additional collections
    all_collections = db.list_collection_names()
    for coll in all_collections:
        score = fuzz.partial_ratio(coll.lower(), query_lower, score_cutoff=50)
        if score > 50 and coll not in matched_collections:
            matched_collections.append(coll)

    log(f"Matched collections: {matched_collections}")
    return matched_collections if matched_collections else None

def fetch_documents_from_multiple_collections(collections, filter_conditions=None, limit=50):
    """Fetch data from multiple MongoDB collections."""
    all_results = {}

    for collection_name in collections:
        collection = db[collection_name]
        query_filter = filter_conditions or {}

        log(f"Fetching from collection: {collection_name}")
        log(f"Using filter: {query_filter}")

        documents = collection.find(query_filter).sort("CreatedAt", -1).limit(limit)

        structured_data = [
            {key: (str(value) if isinstance(value, ObjectId) else value) for key, value in doc.items()}
            for doc in documents
        ]

        all_results[collection_name] = structured_data if structured_data else f"No data found in '{collection_name}'."

    return all_results

def generate_chatbot_response(user_query):
    """Generate chatbot response from multiple MongoDB collections."""
    log(f"Generating response for query: {user_query}")

    collections = identify_collections(user_query)
    if not collections:
        return "Sorry, I couldn't find relevant collections."

    date_filter = create_date_filter_from_query(user_query)
    documents = fetch_documents_from_multiple_collections(collections, filter_conditions=date_filter)

    document_text = ""
    for coll_name, docs in documents.items():
        document_text += f"\n🔹 **{coll_name}**:\n"
        if isinstance(docs, str):
            document_text += f"{docs}\n"
        else:
            document_text += "\n".join(["\n".join([f"- **{key}**: {value}" for key, value in doc.items()]) for doc in docs])

    prompt = f"User Query: {user_query}\n\nContext from MongoDB:\n{document_text}\n\nAssistant: Provide a well-formed paragraph response that is clear and conversational."

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7
        )
        log(f"Generated Chatbot Response: {response['choices'][0]['message']['content'].strip()}")
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f"Error generating response: {e}")
        return f"Error generating response: {e}"

@app.route('/get-answer', methods=['POST'])
def get_answer():
    """API route to receive query and generate response."""
    try:
        data = request.get_json()
        user_query = data.get('query')

        if not user_query:
            return jsonify({"error": "Query is missing"}), 400

        log(f"Received user query: {user_query}")

        response = generate_chatbot_response(user_query)

        db["query_responses"].insert_one({
            "query": user_query,
            "response": response,
            "timestamp": datetime.utcnow()
        })

        return jsonify({"query": user_query, "answer": response})
    except Exception as e:
        log(f"API Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
 
