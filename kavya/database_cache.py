import time
import logging

MAXIMUM_DATA_AGE = 60 # in seconds

class DatabaseCache:
    def __init__(self, app=None):
        if app:
            self.app = app
            self.cache = {}
            self.cache["usage_updates"] = {}
            self.cache["sufficient_balance"] = {}
        else:
            raise ValueError("app must be specified.")
    
    def check_sufficient_balance(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0) -> tuple[bool, dict]:
        """Check if account has sufficient balance for the requested operation without updating the balance."""        
        logging.info("=== CHECKS ACCOUNT IN CACHE ===")
        current_balance = self.cache["sufficient_balance"].get(account_id, None)

        if current_balance is None or current_balance["creation"] < time.time() - MAXIMUM_DATA_AGE:
            logging.info("=== CHECKS DB FOR BALANCE ===")
            result = self.app.db.check_sufficient_balance(account_id, prompt_tokens, completion_tokens, word_count)
            current_balance = result[1]
            current_balance["creation"] = time.time()
        
        logging.info("DATA AGE IN SECONDS : " + str(time.time() - current_balance["creation"]))


        # Calculate sufficient balance directly
        token_balance_in = float(current_balance["token_balance_in"])
        token_balance_out = float(current_balance["token_balance_out"])
        word_balance = int(current_balance["word_balance"])
        transactions = int(current_balance["transactions"])
        
        has_sufficient_balance = (
            token_balance_in >= prompt_tokens and 
            token_balance_out >= completion_tokens and 
            word_balance >= word_count
        )
        
        self.cache["sufficient_balance"][account_id] = {
            "token_balance_in": token_balance_in,
            "token_balance_out": token_balance_out,
            "word_balance": word_balance,
            "transactions": transactions,
            "creation" : current_balance["creation"]
        }

        logging.info("=== ACCOUNT IN CACHE CHECKED ===")
        return has_sufficient_balance, self.cache["sufficient_balance"][account_id]

    def update_usage_with_response(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0):
        cache_start_msg = f"\n====== CACHE UPDATE START ======\n"
        cache_start_msg += f"Account: {account_id}\n"
        cache_start_msg += f"Requested Prompt Tokens: {prompt_tokens}\n"
        cache_start_msg += f"Requested Completion Tokens: {completion_tokens}\n"
        cache_start_msg += f"Requested Word Count: {word_count}\n"
        cache_start_msg += f"=================================="
        logging.info(f"\033[32m{cache_start_msg}\033[0m")  # Using \033[32m for bright green

        current_usage_updates = self.cache["usage_updates"].get(account_id, 
            {
                "prompt_tokens" : 0,
                "completion_tokens" : 0,
                "word_count" : 0,
                "creation" : time.time()
            })

        logging.info(f"Current cache: {current_usage_updates}")

        self.cache["usage_updates"][account_id] = {
            "prompt_tokens": current_usage_updates["prompt_tokens"] + prompt_tokens,
            "completion_tokens": current_usage_updates["completion_tokens"] + completion_tokens,
            "word_count": current_usage_updates["word_count"] + word_count,
            "creation": current_usage_updates["creation"]
        }

        current_usage_updates = self.cache["usage_updates"][account_id]
        logging.info(f"New cache: {current_usage_updates}")
        if self.cache["usage_updates"][account_id]["creation"] < time.time() - MAXIMUM_DATA_AGE:
            # Flush using self.app.db.update_usage_with_response
            logging.info("Flushing cache...")
            self.app.db.update_usage_with_response(
                account_id,
                self.cache["usage_updates"][account_id]["prompt_tokens"],
                self.cache["usage_updates"][account_id]["completion_tokens"],
                self.cache["usage_updates"][account_id]["word_count"]
            )
            del self.cache["usage_updates"][account_id]
        
        cache_msg = f"\n====== CACHE UPDATE SUCCEEDED ======\n"
        cache_msg += f"Account: {account_id}\n"
        cache_msg += f"Prompt Tokens: +{prompt_tokens}\n"
        cache_msg += f"Completion Tokens: +{completion_tokens}\n"
        cache_msg += f"Word Count: +{word_count}\n"
        cache_msg += f"Total Cached Prompt Tokens: {current_usage_updates['prompt_tokens']}\n"
        cache_msg += f"Total Cached Completion Tokens: {current_usage_updates['completion_tokens']}\n"
        cache_msg += f"Total Cached Word Count: {current_usage_updates['word_count']}\n"
        cache_msg += f"=================================="
        logging.info(f"\033[32m{cache_msg}\033[0m")  # Using \033[32m for bright green