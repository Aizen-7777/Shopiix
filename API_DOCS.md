# Shopiiiii Card Checker API

## Overview
Fast & reliable REST API for credit card checking operations.

## Setup

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Run API
```bash
python api.py
```

API will be available at: `http://localhost:8000`

## Authentication

All endpoints require `X-API-Key` header:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://localhost:8000/api/v1/check
```

### Generate API Key

Visit: `http://localhost:8000/api/v1/keys/generate?name=mykey`

Or in code:
```python
from api import KeyManager
key = KeyManager.add_key("my_app")
print(f"New key: {key}")
```

## Endpoints

### 1. Health Check
```
GET /health
```

**Response:**
```json
{
  "status": "online",
  "timestamp": "2024-01-10T12:00:00",
  "version": "1.0.0"
}
```

---

### 2. Check Single Card
```
POST /api/v1/check
Headers: X-API-Key: YOUR_KEY
```

**Request:**
```json
{
  "card": "4111111111111111|12|25|123",
  "site": "https://example.com",
  "proxy": "ip:port:user:pass"
}
```

**Note:** `site` and `proxy` are optional. If not provided, random ones will be selected.

**Response:**
```json
{
  "status": "Charged",
  "card": "411111****1111",
  "message": "Order completed",
  "gateway": "shopify",
  "price": "$50",
  "timestamp": "2024-01-10T12:00:00",
  "request_id": "abc123def456"
}
```

**Status Codes:**
- `Charged` - Card was charged
- `Approved` - Card approved but not charged
- `Declined` - Card declined
- `Dead` - Site error
- `Invalid` - Invalid card format
- `Timeout` - Request timeout

---

### 3. Batch Check Cards
```
POST /api/v1/check/batch
Headers: X-API-Key: YOUR_KEY
```

**Request:**
```json
{
  "cards": [
    "4111111111111111|12|25|123",
    "4111111111111112|12|25|123"
  ],
  "max_concurrent": 5
}
```

**Response:**
```json
{
  "total": 2,
  "charged": 1,
  "approved": 1,
  "declined": 0,
  "results": [
    {
      "status": "Charged",
      "card": "411111****1111",
      "message": "Order completed",
      "gateway": "shopify",
      "price": "$50",
      "timestamp": "2024-01-10T12:00:00",
      "request_id": "batch001"
    }
  ],
  "timestamp": "2024-01-10T12:00:00",
  "request_id": "batch001"
}
```

---

### 4. Test Proxies
```
POST /api/v1/proxy/test
Headers: X-API-Key: YOUR_KEY
```

**Request:**
```json
{
  "proxies": [
    "192.168.1.1:8080:user:pass",
    "192.168.1.2:8080:user:pass"
  ]
}
```

**Response:**
```json
{
  "total": 2,
  "alive": 1,
  "dead": 1,
  "results": {
    "alive_proxies": ["192.168.1.1:8080:user:pass"],
    "dead_proxies": ["192.168.1.2:8080:user:pass"]
  },
  "timestamp": "2024-01-10T12:00:00"
}
```

---

### 5. Get All Sites
```
GET /api/v1/sites
Headers: X-API-Key: YOUR_KEY
```

**Response:**
```json
{
  "total": 150,
  "sites": [
    "https://shop1.com",
    "https://shop2.com"
  ],
  "timestamp": "2024-01-10T12:00:00"
}
```

---

### 6. Get Statistics
```
GET /api/v1/stats
Headers: X-API-Key: YOUR_KEY
```

**Response:**
```json
{
  "total_checks": 1000,
  "total_charged": 150,
  "total_approved": 250,
  "total_declined": 600,
  "uptime": "Running",
  "api_version": "1.0.0"
}
```

---

## Usage Examples

### Python
```python
import requests

API_KEY = "sk_your_api_key"
BASE_URL = "http://localhost:8000"

headers = {"X-API-Key": API_KEY}

# Check single card
response = requests.post(
    f"{BASE_URL}/api/v1/check",
    json={
        "card": "4111111111111111|12|25|123"
    },
    headers=headers
)

print(response.json())
```

### cURL
```bash
curl -X POST http://localhost:8000/api/v1/check \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"card": "4111111111111111|12|25|123"}'
```

### JavaScript/Node.js
```javascript
const API_KEY = "sk_your_api_key";
const BASE_URL = "http://localhost:8000";

fetch(`${BASE_URL}/api/v1/check`, {
  method: "POST",
  headers: {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    card: "4111111111111111|12|25|123"
  })
})
.then(res => res.json())
.then(data => console.log(data));
```

---

## Interactive API Docs

Visit: `http://localhost:8000/docs`

This provides Swagger UI for testing all endpoints interactively.

---

## Error Handling

All errors return appropriate HTTP status codes:

```json
{
  "detail": "Invalid API key"
}
```

**Common Status Codes:**
- `200` - Success
- `400` - Bad request
- `401` - Unauthorized (invalid API key)
- `500` - Server error

---

## Rate Limiting

Currently no rate limiting. Implement based on your needs.

---

## Features

✅ Fast async card checking  
✅ Batch processing support  
✅ Proxy testing  
✅ API key authentication  
✅ Request tracking with IDs  
✅ Full Swagger documentation  
✅ Multiple card formats supported  
✅ Gateway detection  

---

## Performance

- Single check: ~2-5 seconds
- Batch (10 cards, 5 concurrent): ~10-15 seconds
- Proxy test: ~1-3 seconds per proxy

---

## Support

For issues or questions, contact support.
