import argparse
import os

from kavya.controller import Controller

from dotenv import load_dotenv
os.environ["OPENAI_API_KEY"] = "dummy_key_for_testing"
load_dotenv()  # This will load environment variables from .env file

os.environ["TOKENIZERS_PARALLELISM"] = "false"

system_content = (
    "You are a helpful assistant. Respond to the questions as best as you can."
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Threshold for the router",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="What is heavier, a pound of feathers or a kilogram of steel?",
    )
    args = parser.parse_args()
    print(args)

    client = Controller(
        model="gpt-4-1106-preview",
        api_key=os.environ.get("OPENAI_API_KEY") or args.api_key,
    )

    chat_completion = client.chat.completions.create(
        # Or, you can specify these in the model e.g. f"router-{args.router}-{args.threshold}"
        router=args.router,
        threshold=args.threshold,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": args.prompt},
        ],
        temperature=0.7,
    )

    response = chat_completion.choices[0].message.content
    print(f"Router used {chat_completion.model} and received: {response}")
