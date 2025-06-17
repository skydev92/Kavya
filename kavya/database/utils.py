import random
import time


def generate_transaction_id() -> str:
    return f"txn-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
