import os

import litellm
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def get_embedding_for_text(text, model="text-embedding-3-small"):
    """Get embedding for text, checking token limits first."""
    try:
        # Get accurate token count using litellm's default tokenizer
        token_count = litellm.token_counter(text=text)
        print(f"Token count: {token_count}")
        # If over 8K tokens, return None to indicate strong model should be used
        if token_count > 8000:
            print(
                f"Text length {token_count} tokens exceeds maximum 8K tokens, routing to strong model"
            )
            return None

        # For texts under 8K tokens, use small model
        try:
            response = OPENAI_CLIENT.embeddings.create(
                input=[text], model="text-embedding-3-small"
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"Error getting embedding: {str(e)}")
            return None

    except Exception as e:
        # Log the error and return None to route to strong model
        print(f"Error in token counting: {str(e)}")
        return None
