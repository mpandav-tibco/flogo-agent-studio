#!/usr/bin/env python3
"""
Fix ingestion-service.flogo after Flogo UI corrupts VectorDB connection properties.
Run this EVERY TIME after opening/saving the VectorDB connection in the Flogo UI.

Usage:  python3 deployment/fix-vectordb-conn.py
"""
import json, re, os

flogo_path = os.path.join(os.path.dirname(__file__), "..", "services", "agent", "flogo", "ingestion-service.flogo")

with open(flogo_path) as fp:
    app = json.load(fp)

before = len([p for p in app["properties"] if re.match(r'^VectorDB', p["name"])])

# Wipe ALL VectorDB.* properties (UI corrupts names into nested $property refs)
app["properties"] = [p for p in app["properties"] if not re.match(r"^VectorDB", p["name"])]

# Add canonical clean set with literal values — immune to property name corruption
app["properties"].extend([
    {"name": "VectorDB.vectordb-weaviate.Connection_Name",              "type": "string",  "value": "vectordb-weaviate"},
    {"name": "VectorDB.vectordb-weaviate.DB_Provider",                  "type": "string",  "value": "weaviate"},
    {"name": "VectorDB.vectordb-weaviate.Host",                         "type": "string",  "value": "localhost"},
    {"name": "VectorDB.vectordb-weaviate.Port",                         "type": "float64", "value": 18080},
    {"name": "VectorDB.vectordb-weaviate.API_Key",                      "type": "string",  "value": ""},
    {"name": "VectorDB.vectordb-weaviate.Secure_Connection",            "type": "boolean", "value": False},
    {"name": "VectorDB.vectordb-weaviate.Timeout_(seconds)",            "type": "float64", "value": 30},
    {"name": "VectorDB.vectordb-weaviate.Max_Retries",                  "type": "float64", "value": 3},
    {"name": "VectorDB.vectordb-weaviate.Retry_Backoff_(ms)",           "type": "float64", "value": 500},
    {"name": "VectorDB.vectordb-weaviate.HTTP_Scheme",                  "type": "string",  "value": "http"},
    {"name": "VectorDB.vectordb-weaviate.Configure_Embedding_Provider", "type": "boolean", "value": True},
    {"name": "VectorDB.vectordb-weaviate.Embedding_Provider",           "type": "string",  "value": "Ollama"},
    {"name": "VectorDB.vectordb-weaviate.Embedding_API_Key",            "type": "string",  "value": ""},
    {"name": "VectorDB.vectordb-weaviate.Embedding_Base_URL",           "type": "string",  "value": "http://localhost:11434/v1"},
])

# Fix connection name (UI corrupts this to a $property ref)
app["connections"]["vectordb-weaviate"]["name"] = "vectordb-weaviate"

# All settings as literals — immune to $property nesting corruption
app["connections"]["vectordb-weaviate"]["settings"] = {
    "name":                  "vectordb-weaviate",
    "dbType":                "weaviate",
    "host":                  "localhost",
    "port":                  18080,
    "apiKey":                "",
    "useTLS":                False,
    "tlsInsecureSkipVerify": False,
    "tlsServerName":         "",
    "caCert":                "",
    "clientCert":            "",
    "clientKey":             "",
    "timeoutSeconds":        30,
    "maxRetries":            3,
    "retryBackoffMs":        500,
    "grpcPort":              50051,
    "scheme":                "http",
    "username":              "",
    "password":              "",
    "dbName":                "default",
    "defaultMetricType":     "cosine",
    "enableEmbedding":       True,
    "embeddingProvider":     "Ollama",
    "embeddingAPIKey":       "",
    "embeddingBaseURL":      "http://localhost:11434/v1",
}

with open(flogo_path, "w") as fp:
    json.dump(app, fp, indent=2)

after = len([p for p in app["properties"] if re.match(r"^VectorDB", p["name"])])
print(f"Fixed: removed {before} corrupted VectorDB properties, added {after} clean ones.")
print(f"Connection settings: host=localhost port=18080 embedding=Ollama/nomic-embed-text")
print(f"Run 'python3 deployment/fix-vectordb-conn.py' again after any Flogo UI save.")
