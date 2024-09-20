from routellm.controller import Controller

# Initialize the RouteLLM controller
controller = Controller(
    routers=["mf"],  # Using the matrix factorization router
    strong_model="gpt-4o",
    weak_model="gpt-4o-mini",
    suppress_warnings=True  # Add this line to suppress warnings
)

# Define the messages
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {
        "role": "user",
        "content": "Give me the outline for a 5000 word blog post about the future of LLM."
    }
]

# Create a chat completion using RouteLLM with streaming
completion = controller.chat.completions.create(
    model="router-mf-0.11593",  # This specifies the router and threshold
    messages=messages,
    stream=True  # Enable streaming
)

# Print the selected model
print(f"Selected model: {completion.model}")

# Print the response
print("Response:")
for chunk in completion:
    if chunk.choices[0].delta.content is not None:
        print(chunk.choices[0].delta.content, end='', flush=True)

print("\n")  # Add a newline at the end for better formatting

# If you need to access other parts of the response, you can do so like this:
# print(completion.model)  # Prints which model was actually used
# print(completion.usage.total_tokens)  # Prints the total number of tokens used