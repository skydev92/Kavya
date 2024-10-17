import json
from textwrap import dedent
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import asyncio
import logging
import os
import sys
from bs4 import BeautifulSoup
import re
from rich.console import Console
from rich.markdown import Markdown

# Initialize the OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
console = Console()

"""
Set up logging levels:

DEBUG: Detailed information, typically of interest only when diagnosing problems.
INFO: Confirmation that things are working as expected.
WARNING: An indication that something unexpected happened, or indicative of some problem in the near future (e.g., 'disk space low'). The software is still working as expected.
ERROR: Due to a more serious problem, the software has not been able to perform some function.
CRITICAL: A serious error, indicating that the program itself may be unable to continue running.
"""
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


MODEL = "gpt-4o-2024-08-06"

class AgentMemory:
    def __init__(self):
        self.data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self.data[key] = value

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def clear(self):
        self.data.clear()

memory = AgentMemory()

class ContentRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    allowed_html_tags: str = Field(..., min_length=1)
    style_requirements: str = Field(..., min_length=1)

class ContentStrategy(BaseModel):
    strategy: str
    content_scope: str
    recommended_word_count: int
    key_questions: List[str]

class HTMLTagStrategy(BaseModel):
    tags: List[str]

class OutlineSection(BaseModel):
    title: str
    description: str
    content_ideas: List[str]
    multimedia_notes: str

class ContentOutline(BaseModel):
    sections: List[OutlineSection]

class ContentDraft(BaseModel):
    content: str

async def get_content_strategy(request: ContentRequest) -> ContentStrategy:
    content_strategist_prompt = '''
    You are a content strategist. Based on the E-E-A-T framework, provide a content strategy. Include:
    1. Overall strategy
    2. Content scope
    3. Recommended word count (as an integer)
    4. Key questions to answer (as a list)
    Respond in a structured format that matches the ContentStrategy model.
    '''
    
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": dedent(content_strategist_prompt)},
            {"role": "user", "content": request.prompt}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "content_strategy",
                "schema": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "content_scope": {"type": "string"},
                        "recommended_word_count": {"type": "integer"},
                        "key_questions": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["strategy", "content_scope", "recommended_word_count", "key_questions"],
                    "additionalProperties": False
                },
                "strict": True
            }
        }
    )

    return ContentStrategy.model_validate_json(response.choices[0].message.content)

async def get_html_strategy(allowed_html_tags: str, content_strategy: ContentStrategy) -> HTMLTagStrategy:
    html_strategist_prompt = '''
    You are an HTML strategist. Given a list of allowed HTML tags and a content strategy, 
    provide a list of HTML tags that would be most effective for structuring the content.
    Return only the list of HTML tags without any additional explanation or closing tags.
    For example: ["h1", "h2", "p", "ul", "li", "strong", "em", "img"]
    '''
    
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": dedent(html_strategist_prompt)},
            {"role": "user", "content": f"Allowed HTML tags: {allowed_html_tags}\nContent strategy: {content_strategy.model_dump_json()}"}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "html_tag_strategy",
                "schema": {
                    "type": "object",
                    "properties": {
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["tags"],
                    "additionalProperties": False
                },
                "strict": True
            }
        }
    )

    return HTMLTagStrategy.model_validate_json(response.choices[0].message.content)

async def get_content_outline(content_strategy: ContentStrategy, html_strategy: HTMLTagStrategy) -> ContentOutline:
    content_outliner_prompt = '''
    You are a creative content outliner. Given a content strategy and HTML tag strategy, 
    create a detailed, unstructured outline for the content. Your outline should mimic how a real writer 
    might draft their ideas, including:

    1. Section titles
    2. Brief descriptions of what each section should cover
    3. Content ideas and key points for each section
    4. Notes for potential multimedia elements (images, videos, tables, etc.) based on the available HTML tags

    Be creative and think about how to best present the information. Consider the flow of the content
    and how different elements could enhance the reader's understanding or engagement.

    Format your response as a list of sections, each containing:
    - title: The section title
    - description: A brief description of the section's content
    - content_ideas: A list of key points or ideas for the section
    - multimedia_notes: Suggestions for relevant multimedia elements (if applicable)

    Remember to consider the HTML tags available when suggesting multimedia elements.
    '''
    
    log_instruction("Content Outliner", content_outliner_prompt)
    log_to_console("Content Outliner", "Context", f"Content strategy: {content_strategy.model_dump_json()}\nHTML strategy: {html_strategy.model_dump_json()}")
    
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": dedent(content_outliner_prompt)},
            {"role": "user", "content": f"Content strategy: {content_strategy.model_dump_json()}\nHTML strategy: {html_strategy.model_dump_json()}"}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "content_outline",
                "schema": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                    "content_ideas": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "multimedia_notes": {"type": "string"}
                                },
                                "required": ["title", "description", "content_ideas", "multimedia_notes"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["sections"],
                    "additionalProperties": False
                },
                "strict": True
            }
        }
    )

    return ContentOutline.model_validate_json(response.choices[0].message.content)

async def get_content_draft(content_outline: ContentOutline, content_strategy: ContentStrategy, html_strategy: HTMLTagStrategy, style_requirements: str, current_word_count: int = 0) -> ContentDraft:
    content_writer_prompt = f'''
    You are a creative content writer. Given a content outline, content strategy, HTML tag strategy, and style requirements, 
    write a full draft of the content. Use the provided HTML tags to structure the content appropriately.

    The recommended word count is {content_strategy.recommended_word_count}. Your current word count is {current_word_count}.
    Please aim to write approximately {max(content_strategy.recommended_word_count - current_word_count, 100)} words.

    Remember to:
    1. Follow the overall content strategy and address the key questions
    2. Adhere to the style requirements: {style_requirements}
    3. Use the HTML tags effectively to structure and enhance the content
    4. Be creative and engaging in your writing style
    5. Aim for the recommended word count, but prioritize quality and completeness

    Your final output should be a well-structured, engaging piece of content that effectively communicates the topic
    while utilizing the appropriate HTML tags for formatting and multimedia elements.
    '''
    
    messages = [
        {"role": "system", "content": content_writer_prompt},
        {"role": "user", "content": f"Content outline: {content_outline.model_dump_json()}\nContent strategy: {content_strategy.model_dump_json()}\nHTML strategy: {html_strategy.model_dump_json()}\nStyle requirements: {style_requirements}"}
    ]

    print("\n==================================================")
    print("CONTENT WRITER - RESPONSE (Streaming)")
    print("==================================================")

    full_content = ""
    try:
        logger.debug("Creating chat completion stream")
        stream = await client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            stream=True
        )
        logger.debug("Stream created successfully")

        logger.debug("Starting to iterate over stream")
        async for chunk in stream:
            logger.debug(f"Received chunk: {chunk}")
            if chunk.choices[0].delta.content is not None:
                content_chunk = chunk.choices[0].delta.content
                full_content += content_chunk
                console.print(Markdown(content_chunk), end="")
        logger.debug("Finished iterating over stream")

    except Exception as e:
        logger.exception(f"Error during streaming: {str(e)}")
        raise

    print("\n==================================================\n")

    return ContentDraft(content=full_content)

async def content_creation_agent(request: ContentRequest):
    try:
        content_strategy = await get_content_strategy(request)
        log_to_console("Content Strategist", "Response", content_strategy.model_dump_json())
        memory.set("content_strategy", content_strategy)

        html_strategy = await get_html_strategy(request.allowed_html_tags, content_strategy)
        log_to_console("HTML Strategist", "Response", html_strategy.model_dump_json())
        memory.set("html_strategy", html_strategy)

        content_outline = await get_content_outline(content_strategy, html_strategy)
        log_to_console("Content Outliner", "Response", content_outline.model_dump_json())
        memory.set("content_outline", content_outline)

        content_draft = await get_content_draft(content_outline, content_strategy, html_strategy, request.style_requirements)
        total_word_count = len(re.findall(r'\w+', BeautifulSoup(content_draft.content, 'html.parser').get_text()))

        while total_word_count < content_strategy.recommended_word_count * 0.9:  # Allow 10% margin
            additional_content = await get_content_draft(content_outline, content_strategy, html_strategy, request.style_requirements, total_word_count)
            content_draft.content += additional_content.content
            total_word_count = len(re.findall(r'\w+', BeautifulSoup(content_draft.content, 'html.parser').get_text()))

        log_to_console("Content Writer", "Response", content_draft.model_dump_json())
        memory.set("content_draft", content_draft)

        print("\n==================================================")
        print("FINAL HTML OUTPUT")
        print("==================================================")
        pretty_print(content_draft.content, is_final_html=True)
        print("==================================================\n")

        print_debug_report(content_draft.content, content_strategy, html_strategy)

    except Exception as e:
        raise Exception(f"Content Creation Agent error: {str(e)}")

async def main(prompt: str, allowed_html_tags: str, style_requirements: str):
    try:
        request = ContentRequest(
            prompt=prompt,
            allowed_html_tags=allowed_html_tags,
            style_requirements=style_requirements
        )
        await content_creation_agent(request)
    except Exception as e:
        print(json.dumps({"error": f"Content creation failed: {str(e)}"}), file=sys.stderr)
        sys.exit(1)

def log_to_console(agent_name: str, message_type: str, content: str):
    print(f"\n{'=' * 50}")
    print(f"{agent_name.upper()} - {message_type.upper()}")
    print(f"{'=' * 50}")
    pretty_print(content)
    print(f"{'=' * 50}\n")

def pretty_print(content: str, is_final_html: bool = False):
    if is_final_html:
        soup = BeautifulSoup(content, 'html.parser')
        print(soup.prettify())
    else:
        try:
            # Try to parse as JSON
            parsed_content = json.loads(content)
            print(json.dumps(parsed_content, indent=2))
        except json.JSONDecodeError:
            # If it's not JSON, just print it as is
            print(dedent(content).strip())

def log_instruction(agent_name: str, instruction: str):
    print(f"\n{'=' * 50}")
    print(f"{agent_name.upper()} - Instruction")
    print(f"{'=' * 50}")
    print(instruction)
    print(f"{'=' * 50}\n")

def print_debug_report(content: str, content_strategy: ContentStrategy, html_strategy: HTMLTagStrategy):
    # if logging.getLogger().level == logging.DEBUG:
    if True:
        print("\n==================================================")
        print("DEBUG REPORT")
        print("==================================================")

        # Word count
        word_count = len(re.findall(r'\w+', BeautifulSoup(content, 'html.parser').get_text()))
        print(f"Word count: {word_count}")
        print(f"Recommended word count: {content_strategy.recommended_word_count}")
        
        # Evaluation against content strategy
        print("\nEvaluation against content strategy:")
        for question in content_strategy.key_questions:
            print(f"- {question}")
            # You might want to implement a more sophisticated check here
            if any(keyword in content.lower() for keyword in question.lower().split()):
                print("  Likely addressed")
            else:
                print("  Might not be fully addressed")

        # Check HTML tags usage
        print("\nHTML tags usage:")
        soup = BeautifulSoup(content, 'html.parser')
        used_tags = set(tag.name for tag in soup.find_all())
        for tag in html_strategy.tags:
            if tag in used_tags:
                print(f"- {tag}: Used")
            else:
                print(f"- {tag}: Not used")

        print("==================================================\n")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(json.dumps({"error": "Usage: python kavya-content-assistant.py '<prompt>' '<allowed_html_tags>' '<style_requirements>'"}), file=sys.stderr)
        sys.exit(1)
    
    prompt = sys.argv[1]
    allowed_html_tags = sys.argv[2]
    style_requirements = sys.argv[3]
    
    asyncio.run(main(prompt, allowed_html_tags, style_requirements))
