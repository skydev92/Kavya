import jwt
import os
import base64
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

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

def decode_jwt(token: str, public_key_b64: str, verify: bool = True) -> dict:
    """Decode and optionally verify a JWT token"""
    if verify and not verify_jwt_signature(token, public_key_b64):
        raise jwt.InvalidTokenError("Invalid signature")
    return jwt.decode(token, options={"verify_signature": False})

def main():
    load_dotenv()
    token = os.getenv('JWT_TEST_TOKEN')
    public_key_b64 = os.getenv('JWT_PUBLIC_KEY_B64')
    
    if not token or not public_key_b64:
        print("Missing JWT_TEST_TOKEN or JWT_PUBLIC_KEY_B64 in environment")
        return
    
    try:
        payload = decode_jwt(token, public_key_b64)
        print("\nToken verification successful!")
        print("\nDecoded payload:")
        print("================")
        for key, value in payload.items():
            print(f"{key}: {value}")
    except Exception as e:
        print(f"\nToken verification failed: {e}")

if __name__ == "__main__":
    main() 