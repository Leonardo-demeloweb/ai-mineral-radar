#!/usr/bin/env python3
"""
MineralRadar — Connection Test Script
=========================================
Testa conexões com Redis, MongoDB e OpenSearch.

Uso:
    python scripts/test_connections.py

Ou com variáveis de ambiente:
    OPENSEARCH_ENDPOINT=https://... OPENSEARCH_USER=admin OPENSEARCH_PASSWORD=... python scripts/test_connections.py
"""

import os
import sys
from datetime import datetime

# Cores para output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def print_header(text: str):
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}{text:^60}{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

def print_success(text: str):
    print(f"{GREEN}✅ {text}{RESET}")

def print_error(text: str):
    print(f"{RED}❌ {text}{RESET}")

def print_warning(text: str):
    print(f"{YELLOW}⚠️  {text}{RESET}")

def print_info(text: str):
    print(f"{BLUE}ℹ️  {text}{RESET}")


def test_redis():
    """Testa conexão com Redis."""
    print_header("REDIS CONNECTION TEST")
    
    try:
        import redis
    except ImportError:
        print_error("redis package not installed. Run: pip install redis")
        return False
    
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", 6379))
    password = os.getenv("REDIS_PASSWORD", "redis-supplyradar2-123")
    
    print_info(f"Connecting to Redis at {host}:{port}...")
    
    try:
        client = redis.Redis(
            host=host,
            port=port,
            username="default",  # Required for Redis 7+
            password=password,
            decode_responses=True,
            socket_timeout=5
        )
        
        # Test PING
        result = client.ping()
        if result:
            print_success("PING successful")
        
        # Test SET/GET
        test_key = f"test:connection:{datetime.now().isoformat()}"
        client.set(test_key, "MineralRadar connection test", ex=60)
        value = client.get(test_key)
        if value:
            print_success(f"SET/GET successful: {value}")
        
        # Get server info
        info = client.info("server")
        print_success(f"Redis version: {info.get('redis_version', 'unknown')}")
        
        # Cleanup
        client.delete(test_key)
        client.close()
        
        print_success("Redis connection OK!")
        return True
        
    except redis.ConnectionError as e:
        print_error(f"Connection failed: {e}")
        return False
    except redis.AuthenticationError as e:
        print_error(f"Authentication failed: {e}")
        return False
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return False


def test_mongodb():
    """Testa conexão com MongoDB."""
    print_header("MONGODB CONNECTION TEST")
    
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConnectionFailure, OperationFailure
    except ImportError:
        print_error("pymongo package not installed. Run: pip install pymongo")
        return False
    
    uri = os.getenv("MONGODB_URI", "mongodb://admin:mongo-supplyradar2-123@localhost:27017")
    database = os.getenv("MONGODB_DATABASE", "supplyradar")
    
    print_info(f"Connecting to MongoDB...")
    print_info(f"Database: {database}")
    
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        
        # Test connection
        client.admin.command('ping')
        print_success("PING successful")
        
        # Get server info
        server_info = client.server_info()
        print_success(f"MongoDB version: {server_info.get('version', 'unknown')}")
        
        # Test database access
        db = client[database]
        collections = db.list_collection_names()
        print_success(f"Database '{database}' accessible. Collections: {len(collections)}")
        
        # Test write/read
        test_collection = db["_connection_test"]
        test_doc = {
            "test": True,
            "timestamp": datetime.now(),
            "message": "MineralRadar connection test"
        }
        result = test_collection.insert_one(test_doc)
        print_success(f"INSERT successful: {result.inserted_id}")
        
        # Cleanup
        test_collection.delete_one({"_id": result.inserted_id})
        
        client.close()
        
        print_success("MongoDB connection OK!")
        return True
        
    except ConnectionFailure as e:
        print_error(f"Connection failed: {e}")
        return False
    except OperationFailure as e:
        print_error(f"Operation failed: {e}")
        return False
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return False


def test_opensearch():
    """Testa conexão com OpenSearch."""
    print_header("OPENSEARCH CONNECTION TEST")
    
    try:
        from opensearchpy import OpenSearch, AuthenticationException, ConnectionError as OSConnectionError
    except ImportError:
        print_error("opensearch-py package not installed. Run: pip install opensearch-py")
        return False
    
    endpoint = os.getenv("OPENSEARCH_ENDPOINT", "")
    user = os.getenv("OPENSEARCH_USER", "admin")
    password = os.getenv("OPENSEARCH_PASSWORD", "")
    
    if not endpoint:
        print_warning("OPENSEARCH_ENDPOINT not set. Skipping OpenSearch test.")
        print_info("Set environment variable: export OPENSEARCH_ENDPOINT=https://your-cluster-endpoint")
        return None
    
    if not password:
        print_warning("OPENSEARCH_PASSWORD not set. Skipping OpenSearch test.")
        return None
    
    # Clean endpoint
    endpoint = endpoint.rstrip('/')
    if not endpoint.startswith('http'):
        endpoint = f"https://{endpoint}"
    
    print_info(f"Connecting to OpenSearch at {endpoint}...")
    print_info(f"User: {user}")
    
    try:
        client = OpenSearch(
            hosts=[endpoint],
            http_auth=(user, password),
            use_ssl=True,
            verify_certs=True,
            ssl_show_warn=False,
            timeout=30
        )
        
        # Test connection
        info = client.info()
        print_success(f"Cluster: {info.get('cluster_name', 'unknown')}")
        print_success(f"Version: {info.get('version', {}).get('number', 'unknown')}")
        
        # Get cluster health
        health = client.cluster.health()
        status = health.get('status', 'unknown')
        status_color = GREEN if status == 'green' else (YELLOW if status == 'yellow' else RED)
        print(f"{status_color}✅ Cluster health: {status}{RESET}")
        print_success(f"Nodes: {health.get('number_of_nodes', 0)}")
        print_success(f"Data nodes: {health.get('number_of_data_nodes', 0)}")
        
        # List indices
        indices = client.cat.indices(format='json')
        user_indices = [idx for idx in indices if not idx['index'].startswith('.')]
        print_success(f"User indices: {len(user_indices)}")
        
        if user_indices:
            print_info("Indices found:")
            for idx in user_indices[:5]:
                print(f"    - {idx['index']} ({idx.get('docs.count', 0)} docs)")
            if len(user_indices) > 5:
                print(f"    ... and {len(user_indices) - 5} more")
        
        print_success("OpenSearch connection OK!")
        return True
        
    except AuthenticationException as e:
        print_error(f"Authentication failed: {e}")
        return False
    except OSConnectionError as e:
        print_error(f"Connection failed: {e}")
        return False
    except Exception as e:
        print_error(f"Unexpected error: {type(e).__name__}: {e}")
        return False


def main():
    print_header("SUPPLYRADAR 2.0 - CONNECTION TESTS")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    results = {}
    
    # Test Redis
    results['redis'] = test_redis()
    
    # Test MongoDB
    results['mongodb'] = test_mongodb()
    
    # Test OpenSearch
    results['opensearch'] = test_opensearch()
    
    # Summary
    print_header("SUMMARY")
    
    for service, status in results.items():
        if status is True:
            print_success(f"{service.upper()}: Connected")
        elif status is False:
            print_error(f"{service.upper()}: Failed")
        else:
            print_warning(f"{service.upper()}: Skipped (not configured)")
    
    # Exit code
    if all(s in (True, None) for s in results.values()):
        print(f"\n{GREEN}All configured services are reachable!{RESET}")
        return 0
    else:
        print(f"\n{RED}Some connections failed. Check the errors above.{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
