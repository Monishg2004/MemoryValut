import os
import logging
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone, Index
from langchain.docstore.document import Document
from langchain.text_splitter import CharacterTextSplitter
import uuid
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configure Google Gemini API
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

# Initialize Pinecone
try:
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    logger.info("Pinecone initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Pinecone: {str(e)}")
    pc = None

class SentenceTransformerEmbeddings:
    def __init__(self, model_name):
        self.model = SentenceTransformer(model_name)

    def embed_documents(self, texts):
        return self.model.encode(texts).tolist()

    def embed_query(self, text):
        return self.model.encode([text])[0].tolist()

embeddings = SentenceTransformerEmbeddings('sentence-transformers/all-mpnet-base-v2')

def get_vectorstore():
    index_name = "memoryvalut"
    try:
        index = pc.Index(index_name)
        logger.info(f"Successfully connected to Pinecone index: {index_name}")
        return index
    except Exception as e:
        logger.error(f"Failed to connect to Pinecone index: {str(e)}")
        return None

def add_document_to_pinecone(text: str, metadata: dict):
    new_doc = Document(page_content=text, metadata=metadata)
    text_splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=100)
    chunks = text_splitter.split_documents([new_doc])
    document_id = str(uuid.uuid4())
    
    vectors = []
    for i, chunk in enumerate(chunks):
        chunk.metadata["document_id"] = document_id
        chunk.metadata["chunk_id"] = i
        chunk.metadata["text"] = chunk.page_content  # Store the text in metadata
        vector = embeddings.embed_documents([chunk.page_content])[0]
        vectors.append((f"{document_id}_{i}", vector, chunk.metadata))
    
    index = get_vectorstore()
    if index is None:
        raise Exception("Failed to connect to Pinecone index")
    
    try:
        index.upsert(vectors=vectors)
        logger.info(f"Successfully added document with ID: {document_id}")
    except Exception as e:
        logger.error(f"Failed to upsert vectors: {str(e)}")
        raise

def get_llm_response(query: str):
    index = get_vectorstore()
    if index is None:
        raise Exception("Failed to connect to Pinecone index")
    
    query_embedding = embeddings.embed_query(query)
    
    try:
        results = index.query(vector=query_embedding, top_k=5, include_metadata=True)
        # logger.info(f"Query results: {results}")
    except Exception as e:
        logger.error(f"Failed to query Pinecone: {str(e)}")
        raise
    
    # Extract memory text from results
    context = " ".join([match.get('metadata', {}).get('text', '') for match in results['matches']])
    
    if not context:
        # If no context was retrieved, return a message indicating no memories found
        return "I'm sorry, but I don't have any memories to share right now. Please add some memories first."

    # Use the Gemini API for generating a response
    model = genai.GenerativeModel('gemini-pro')

    # Use context in the prompt
    prompt = f"""
    You are an AI meant to help Alzheimer's patients remember their memories.
    The user is asking: "{query}"

    They might ask for more details about a memory that they remember a little of.
    Be kind and considerate.
    
    Retieve the safety ratings content too and provide to user
    
    Here is the memory to recall: "{context}"

    USE AS MUCH DETAIL AS POSSIBLE. You want them to feel like they are living there again.
    
    Respond in the second person.
    Make it vivid and paraphrase.

    REMEMBER THE INFORMATION THAT THE USER TELLS YOU TO.

    Do NOT:
    - Mention anything about being an AI.
    - Mention anything about context.
    - Make up ANY FALSE INFORMATION.

    If you can't find any relevant memories, tell them to go to the add memory page and have them or a family member add a memory.
    """

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Failed to generate response from Gemini: {str(e)}")
        raise

@app.route("/postMemory", methods=['GET', 'POST'])
def post_memory():
    if request.method == 'POST':
        data = request.json
        text = data.get("text")
        metadata = data.get("metadata", {})
    else:  # GET method
        text = request.args.get("text")
        metadata = {}
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    try:
        add_document_to_pinecone(text, metadata)
        return jsonify({"message": "Memory added successfully"}), 200
    except Exception as e:
        logger.error(f"Error in post_memory: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/query", methods=['GET'])
def query_memory():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    try:
        response = get_llm_response(query)
        return jsonify({"response": response}), 200
    except Exception as e:
        logger.error(f"Error in query_memory: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
