from flask import Flask, request, jsonify, send_from_directory
import os
import openai
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId
from fuzzywuzzy import fuzz
from flask_cors import CORS
from datetime import datetime

# Load environment variables from .env file
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

def get_all_collections():
    """Fetch all collection names in the database."""
    return db.list_collection_names()

def get_fields_for_collection(collection_name, sample_size=10):
    """Extract fields from multiple documents in a collection."""
    collection = db[collection_name]
    documents = collection.find().limit(sample_size)

    all_fields = set()
    for doc in documents:
        all_fields.update(doc.keys())

    return list(all_fields)

def identify_collection(query):
    """Identify the best-matching collection based on the user query."""
    query_lower = query.lower().strip()

    collection_mapping = {
        "alarm": "alarmHistory",
        "production": "productionData",
       "oee": "oeelog1",
        "downtime": "downtimeData",
        "maintenance": "maintenanceLogs",
        "alerts": "alerttype, alertname",
    }

    for keyword, coll_name in collection_mapping.items():
        if keyword in query_lower:
            return coll_name

    all_collections = get_all_collections()
    best_match = None
    highest_score = 0
    for coll in all_collections:
        score = fuzz.partial_ratio(coll.lower(), query_lower)
        if score > highest_score and score > 50:
            best_match = coll
            highest_score = score

    return best_match if best_match else None

def fetch_documents_from_collection(collection_name, filter_conditions=None, date_filter=None, limit=5):
    """Fetch documents sorted by timestamp field."""
    try:
        collection = db[collection_name]
        query_filter = filter_conditions or {}

        documents = collection.find(query_filter).limit(limit)

        structured_data = []
        for doc in documents:
            cleaned_doc = {key: (str(value) if isinstance(value, ObjectId) else value) for key, value in doc.items()}
            structured_data.append(cleaned_doc)

        return structured_data if structured_data else "No relevant data found."
    except Exception as e:
        return f"Error fetching documents: {e}"

def correct_grammar(text):
    """Use OpenAI API to correct grammar."""
    if not text.strip():
        return text

    prompt = f"Correct the grammar of the following text while preserving its meaning: \n{text}"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.5
        )
        corrected_text = response['choices'][0]['message']['content'].strip()
        return corrected_text
    except Exception as e:
        return f"Error correcting grammar: {e}"

def generate_chatbot_response(user_query):
    """Generate the chatbot response."""
    collection_name = identify_collection(user_query)
    if not collection_name:
        return "Sorry, I couldn't find a relevant collection."

    filter_conditions = {}

    documents = fetch_documents_from_collection(collection_name, filter_conditions)
    if isinstance(documents, str) and "Error" in documents:
        return documents

    document_text = "\n".join(
        ["\n".join([f"- **{key}**: {value}" for key, value in doc.items()]) for doc in documents]
    )

    prompt = (
        f"User Query: {user_query}\n\n"
        f"Context from MongoDB:\n{document_text}\n\n"
        "Assistant: Provide a well-formed paragraph response that is clear and conversational."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.7
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        return f"Error generating response: {e}"

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')  # Serve the index.html directly from the root folder

@app.route('/get-answer', methods=['POST'])
def get_answer():
    """API route to receive query and generate response."""
    try:
        data = request.get_json()  # Get the JSON payload
        user_query = data.get('query')

        if not user_query:
            return jsonify({"error": "Query is missing"}), 400

        corrected_query = correct_grammar(user_query)

        # Check if query already exists in the database
        query_collection = db["query_responses"]  # Collection for storing queries and responses
        existing_entry = query_collection.find_one({"query": corrected_query})

        if existing_entry:
            return jsonify({
                "corrected_query": corrected_query,
                "answer": existing_entry["response"],
                "timestamp": existing_entry["timestamp"]
            })

        # Generate response if not found in database
        response = generate_chatbot_response(corrected_query)

        # Store new query, response, and timestamp
        query_data = {
            "query": corrected_query,
            "response": response,
            "timestamp": datetime.utcnow()
        }
        query_collection.insert_one(query_data)

        return jsonify({
            "corrected_query": corrected_query,
            "answer": response,
            "timestamp": query_data["timestamp"]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
