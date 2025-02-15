from typing import Optional
import jwt
import os
import base64
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

security = HTTPBearer()

def verify_jwt_signature(token: str, public_key_b64: str) -> bool:
    """Verify JWT signature using PS256"""
    try:
        # Split token and create signing input
        header_b64, payload_b64, signature_b64 = token.split('.')
        signing_input = f"{header_b64}.{payload_b64}".encode('ascii')
        
        # Decode and load public key
        key_bytes = base64.b64decode(public_key_b64)
        public_key = serialization.load_pem_public_key(key_bytes)
        
        # Decode signature
        signature = base64.urlsafe_b64decode(signature_b64 + '=' * (-len(signature_b64) % 4))
        
        # Verify with PS256
        public_key.verify(
            signature,
            signing_input,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False

def decode_jwt(token: str, public_key_b64: str) -> dict:
    """Decode and verify a JWT token"""
    # First verify signature
    if not verify_jwt_signature(token, public_key_b64):
        raise jwt.InvalidTokenError("Invalid signature")
    
    # Decode without verification since we already verified
    payload = jwt.decode(token, options={"verify_signature": False})
    
    # Verify sub claim is a valid integer
    try:
        sub = str(payload.get('sub', ''))
        if not sub or not sub.isdigit():
            raise ValueError("Invalid sub claim")
    except Exception:
        raise jwt.InvalidTokenError("Invalid sub claim")
        
    return payload

class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, credentials: HTTPAuthorizationCredentials = Depends(security)):
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="Missing authentication credentials",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication scheme. Use Bearer token",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        try:
            public_key_b64 = os.getenv('JWT_PUBLIC_KEY_B64')
            if not public_key_b64:
                raise HTTPException(status_code=500, detail="JWT public key not configured")
                
            payload = decode_jwt(credentials.credentials, public_key_b64)
            return payload
            
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=401,
                detail=str(e),
                headers={"WWW-Authenticate": "Bearer"}
            )
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"}
            ) 