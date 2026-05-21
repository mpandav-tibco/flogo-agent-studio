#!/usr/bin/env python3
"""
Fix VectorDB connection properties corrupted by the Flogo UI in agent flogo files.
Run this EVERY TIME after opening/saving a VectorDB connection in the Flogo UI.

Usage:  python3 deployment/fix-vectordb-conn.py
"""
import json, re, os

CLEAN_PROPERTIES = [
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
]

CLEAN_SETTINGS = {
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

FLOGO_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "agent", "flogo")
TARGET_FILES = [
    "agent-chat-service.flogo",
    "ingestion-service.flogo",
]

for filename in TARGET_FILES:
    flogo_path = os.path.join(FLOGO_DIR, filename)
    if not os.path.exists(flogo_path):
        print(f"SKIP  {filename} (not found)")
        continue

    with open(flogo_path) as fp:
        app = json.load(fp)

    if "vectordb-weaviate" not in app.get("connections", {}):
        print(f"SKIP  {filename} (no vectordb-weaviate connection)")
        continue

    before = sum(1 for p in app["properties"] if re.match(r"^VectorDB", p["name"]))
    app["properties"] = [p for p in app["properties"] if not re.match(r"^VectorDB", p["name"])]
    app["properties"].extend(CLEAN_PROPERTIES)
    app["connections"]["vectordb-weaviate"]["name"] = "vectordb-weaviate"
    app["connections"]["vectordb-weaviate"]["settings"] = CLEAN_SETTINGS

    with open(flogo_path, "w") as fp:
        json.dump(app, fp, indent=2)

    after = sum(1 for p in app["properties"] if re.match(r"^VectorDB", p["name"]))
    print(f"Fixed {filename}: removed {before} corrupted VectorDB properties, added {after} clean ones.")
