#!/usr/bin/env python3
import asyncio
import aiohttp
import argparse
import time
import json
from datetime import datetime
import logging
from typing import Dict, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Test request payload
TEST_PAYLOAD = {
    "model": "kavya-m1",
    "messages": [
        {
            "role": "system",
            "content": "<RESPONSE_RULES>\nGenerate a response that addresses the <TASK>\nIf <SELECTED_CONTENT> exists, use only that content to answer the <TASK>, ignoring additional <CONTEXT>.\n</RESPONSE_RULES>\n\n<HTML_FORMATTING>\nHTML Requirements:\nUse only these tags: blockquote, em, h1, h2, h3, li, ol, p, strong, ul.\nEnsure proper tag nesting.\nUse semantic HTML.\nNo inline styles.\nFirst word must be HTML tag.\nOutput raw HTML without markdown code blocks (no ```html or ``` wrapping).\n</HTML_FORMATTING>\n\n<CONTENT_STRUCTURE>\nOrganize information logically.\nUse paragraphs.\nMaintain consistent formatting.\n</CONTENT_STRUCTURE>\n\n<TONE>\nProfessional, Clear\n</TONE>\n\n<REFERENCE_GUIDELINES>\nGenerate new text that flows naturally with <CONTEXT>.\nEnsure requested percentage of new content.\n</REFERENCE_GUIDELINES>\n\n<CONTEXT_REQUIREMENTS>\nReplace @@@cursor@@@ with content for <TASK>.\nReturn ONLY text replacing @@@cursor@@@ - surrounding text is READ-ONLY.\nNever copy context text.\nVerify zero duplication.\nAnalyze CONTEXT thoroughly.\nEnsure response flows naturally.\nNever include the string `@@@cursor@@@` in your response.\n</CONTEXT_REQUIREMENTS>\n\n"
        },
        {
            "role": "user",
            "content": "<TASK>\nwrite 1 word\n</TASK>\n\n<CONTEXT>\n<p>@@@cursor@@@</p>\n<h3>Feature One</h3>\n<h3>Feature Two</h3>\n<h3>Feature Three</h3>\n</CONTEXT>\n\n<CONTEXT_REQUIREMENTS>\nReplace @@@cursor@@@ with content for <TASK>.\nReturn ONLY text replacing @@@cursor@@@ - surrounding text is READ-ONLY.\nNever copy context text.\nVerify zero duplication.\nAnalyze CONTEXT thoroughly.\nEnsure response flows naturally.\nNever include the string `@@@cursor@@@` in your response.\n</CONTEXT_REQUIREMENTS>\n\n<INSTRUCTIONS>\nThe response must follow the language code - en.\n</INSTRUCTIONS>"
        }
    ],
    "stream": True,
    "max_tokens": 16384,
    "stop": [],
    "allowed_html_tags": "blockquote, em, h1, h2, h3, li, ol, p, strong, ul"
}

# Headers for the request
HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Authorization': 'Bearer eyJhbGciOiJQUzI1NiIsImtpZCI6IjR6RGRXS1pGNGRfbXprcVVMc2tYb3ItcE96bGRITFN0WGI1Q1pUX3d4UnMifQ.eyJpc3MiOiJodHRwczpcL1wvZHhwci5jb20iLCJzdWIiOiIxIiwiYXVkIjoiaHR0cHM6XC9cL3BhY2thZ2VzLmR4cHIuY29tIiwic2NvcGUiOiJkeHByXC9keHByX2J1aWxkZXIiLCJkeHByX3RpZXIiOiJncm93dGgiLCJqdGkiOiIxNWIzZTUyNWY5NzA3NDZlYzcwZTM3YWEyMGZhMjg5YzJkZGM1ZGUzYzlkYWJmODcxYmFkNGNhMDA4MDcyMjM3In0.uJP1E05QbTyZXFwzOyQESL1-X3eZpl4BadN8xecOHVf4Cm9WoCGRN5EGQzWHD1zk4NTgXt5GkFAKYEOjWHogCWUCEE51ihm_sjlPs4mdk1w7HYo7UIkchq7b4X3ZLyqM3nii0srLpCHJ2fj5ZYU3BKoKHMZNp4iqouV2DA3jnPQd58Jg0r8TqlVzvfghYfSaFG8FFPTaK_3XRK0maV5ZAXcO_m8C26shV-l3rN29wvc07KZAj-zItNcS7pGdJLqn5DJ9-Qrb5EZPmiWPJNecrePguLNVtpcGdas3L5uCUTizLdi8i-C9icNH-WmlTCeTU1JKL4wm_0mEIzCNmU7u5VdX4t2pud4fkYOiI2qXQSg83pAYSdACCWLBJSPStaIBiUJHZFBAbpl6CWYtkM3uSJQoqQFaYb_1t2j1dCDUHQhsVySUXrhX3Nf3AFMCNFjGqWE_5XSWt-GBYwmBsj-jOQqXceOmE4RslE_JXwrX2lcqGxX-SJYOdf4FwVb9f7Ne7QDfbPPtUxZmhGuQQNZ2d0ejhYimVzHrCH6NR07YrViQ7aYdx_N_D3nYiG19Bmbu5dq-Nj_q4qMA1LljwkFRRd_wPT5NLrqb1m1SH8BgGoUdrv35HdxA7tLlxQ5IXuvHu-cAExCQII42nMskSdLcOGzSWg92tkSt9J31lI2XSFw',
    'Connection': 'keep-alive',
    'Content-Type': 'application/json',
    'Origin': 'http://localhost:8080',
    'Referer': 'http://localhost:8080/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"'
}

class LoadTestResults:
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_time = 0
        self.response_times: List[float] = []
        self.errors: Dict[str, int] = {}
        self.db_errors: Dict[str, int] = {}  # Track database-specific errors
        
    def add_success(self, response_time: float):
        self.successful_requests += 1
        self.total_requests += 1
        self.response_times.append(response_time)
        
    def add_failure(self, error: str):
        self.failed_requests += 1
        self.total_requests += 1
        
        # Track database errors separately
        if any(db_err in error.lower() for db_err in [
            "could not serialize access",
            "deadlock detected",
            "concurrent update"
        ]):
            self.db_errors[error] = self.db_errors.get(error, 0) + 1
        else:
            self.errors[error] = self.errors.get(error, 0) + 1
        
    def print_results(self):
        avg_response_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        success_rate = (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0
        
        logging.info("\n=== Load Test Results ===")
        logging.info(f"Total Requests: {self.total_requests}")
        logging.info(f"Successful Requests: {self.successful_requests}")
        logging.info(f"Failed Requests: {self.failed_requests}")
        logging.info(f"Success Rate: {success_rate:.2f}%")
        logging.info(f"Average Response Time: {avg_response_time:.2f}s")
        
        if self.db_errors:
            logging.info("\nDatabase Errors:")
            for error, count in self.db_errors.items():
                logging.info(f"  {error}: {count}")
        
        if self.errors:
            logging.info("\nOther Errors:")
            for error, count in self.errors.items():
                logging.info(f"  {error}: {count}")

async def make_request(session: aiohttp.ClientSession, url: str, results: LoadTestResults) -> None:
    start_time = time.time()
    try:
        async with session.post(url, json=TEST_PAYLOAD, headers=HEADERS) as response:
            # For streaming responses, we need to read all data
            error_text = ""
            try:
                async for line in response.content:
                    if line:
                        try:
                            data = json.loads(line.decode('utf-8').strip().removeprefix('data: '))
                            if 'error' in data:
                                error_text = json.dumps(data['error'])
                                break
                        except:
                            pass
            except Exception as e:
                error_text = str(e)
            
            if response.status == 200 and not error_text:
                results.add_success(time.time() - start_time)
            else:
                results.add_failure(error_text or f"HTTP {response.status}")
    except Exception as e:
        results.add_failure(str(e))

async def run_load_test(url: str, num_requests: int, concurrency: int) -> LoadTestResults:
    results = LoadTestResults()
    
    # Create a connection pool with keep-alive
    conn = aiohttp.TCPConnector(
        limit=concurrency,
        enable_cleanup_closed=True,
        force_close=False,
        keepalive_timeout=30
    )
    
    timeout = aiohttp.ClientTimeout(total=60)  # Increase timeout for long-running requests
    
    async with aiohttp.ClientSession(
        connector=conn,
        timeout=timeout,
        headers=HEADERS
    ) as session:
        # Create tasks for all requests
        tasks = []
        for _ in range(num_requests):
            tasks.append(make_request(session, url, results))
        
        # Run tasks in batches based on concurrency
        for i in range(0, len(tasks), concurrency):
            batch = tasks[i:i + concurrency]
            await asyncio.gather(*batch)
            
            # Print progress
            completed = min(i + concurrency, num_requests)
            logging.info(f"Progress: {completed}/{num_requests} requests completed")
            
            # Small delay between batches to avoid overwhelming the server
            if i + concurrency < num_requests:
                await asyncio.sleep(0.1)
    
    return results

def main():
    parser = argparse.ArgumentParser(description='Load test for Kavya API')
    parser.add_argument('--url', default='http://localhost:8089/v1/chat/completions',
                      help='URL to test (default: http://localhost:8089/v1/chat/completions)')
    parser.add_argument('--requests', type=int, default=15,
                      help='Number of requests to make (default: 30)')
    parser.add_argument('--concurrency', type=int, default=5,
                      help='Number of concurrent requests (default: 5)')
    parser.add_argument('--delay', type=float, default=0.1,
                      help='Delay between batches in seconds (default: 0.1)')
    args = parser.parse_args()
    
    logging.info(f"Starting load test with {args.requests} requests and concurrency of {args.concurrency}")
    logging.info(f"Target URL: {args.url}")
    
    # Run the load test
    results = asyncio.run(run_load_test(args.url, args.requests, args.concurrency))
    
    # Print results
    results.print_results()

if __name__ == "__main__":
    main() 