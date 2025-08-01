# Kavya

**Craft Your Story with AI Elegance**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-brightgreen.svg)]()
[![Build Status](https://img.shields.io/badge/build-passing-success.svg)]()

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Demo](#demo)
- [Installation](#installation)
- [Usage](#usage)
- [Advanced Features](#advanced-features)
- [Security and Compliance](#security-and-compliance)
- [API Documentation](#api-documentation)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Introduction

**Kavya** is an AI-powered content authoring assistant designed to transform your ideas into compelling web content effortlessly. By blending creativity with cutting-edge AI technology, Kavya empowers writers, marketers, and creators to produce high-quality content with ease and efficiency.

---

## Features

- **AI-Assisted Writing**: Generate drafts, suggestions, and complete articles.
- **Advanced Prompt Optimization**: Utilize Kavya to optimize prompts and create advanced multi-model, multi-prompt sequences.
- **Content Enhancement**: Automatically improve content quality, style, and coherence.
- **Content Optimization**: Improve SEO and readability.
- **Multi-language Support**: Write and translate content in multiple languages.
- **Tone and Style Customization**: Match your brand voice.
- **Collaboration Tools**: Work with team members in real-time.
- **CMS Integration**: Seamlessly integrate with platforms like WordPress and Drupal.
- **Template Library**: Access a variety of content templates for blogs, ads, emails, and more.
- **Plagiarism Checker**: Ensure your content is original.
- **Version Control**: Track changes and revert to previous versions.
- **Export Options**: Download content in various formats (PDF, DOCX, HTML).

---

## Installation

### Prerequisites

- **Operating System**: Windows, macOS, or Linux
- **Python**: 3.11 or higher
- **Docker**: Latest stable version (optional)
- **PostgreSQL**: 15 or higher

### Steps to Install and Run Kavya

1. **Clone the Repository**
   ```bash
   git clone https://github.com/your-username/kavya.git
   cd kavya
   ```

2. **Set Up PostgreSQL Database**

   a. **Install PostgreSQL** (macOS)
   ```bash
   # Install PostgreSQL 15 or higher
   brew install postgresql@15
   
   # Start PostgreSQL service
   brew services start postgresql@15
   
   # Verify installation
   postgres --version
   ```

   b. **Install PostgreSQL** (Ubuntu/Debian)
   ```bash
   sudo apt-get update
   sudo apt-get install postgresql-15 postgresql-contrib
   sudo systemctl start postgresql
   sudo systemctl enable postgresql
   ```

   c. **Set Up Local Database**
   ```bash
   # Run the database setup script
   python scripts/setup_local_db.py
   
   # This will:
   # - Create a database user (kavya_user)
   # - Create a database (kavya_db)
   # - Set up the password (postgres)
   # - Create/update your .env file
   ```

   d. **Test Database Connection**
   ```bash
   # Connect to the database
   psql -h localhost -U kavya_user -d kavya_db
   # When prompted for password, enter: postgres
   
   # You should see the PostgreSQL prompt:
   kavya_db=>
   # Type \q to exit
   ```

3. **Install Dependencies**
   ```bash
   # Create and activate virtual environment (recommended)
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
   # Install Python dependencies
   pip install -r requirements.txt
   ```

4. **Run Kavya**

   **Option 1: Direct Python (Recommended for Development)**
   ```bash
   # Ensure PostgreSQL is running
   brew services start postgresql@15  # macOS
   # or
   sudo systemctl start postgresql    # Linux
   
   # Start Kavya server
   python -m kavya.openai_server --verbose --config config.yaml --port 8089
   ```

   **Option 2: Docker (Optional)**
   ```bash
   # Build the Docker image
   docker build -t kavya .
   
   # Run with Docker (ensure PostgreSQL is accessible)
   docker run --rm -it \
     --name kavya \
     -p 8089:8089 \
     --env-file .env \
     kavya
   ```

5. **Access Kavya**
   Open your web browser and navigate to `http://localhost:8089` to start using Kavya.

### Troubleshooting

If you encounter database issues:

1. **Check PostgreSQL Service**
   ```bash
   # macOS
   brew services list | grep postgresql
   brew services start postgresql@15
   
   # Linux
   sudo systemctl status postgresql
   sudo systemctl start postgresql
   ```

2. **Test Connection**
   ```bash
   psql -h localhost -U kavya_user -d kavya_db
   # Password: postgres
   ```

3. **Common Solutions**
   - Ensure PostgreSQL service is running
   - Verify your `.env` file has the correct `DATABASE_URL`
   - Check that the database user and database exist
   - Ensure firewall allows connections to PostgreSQL (port 5432)

---

## Advanced Features

### Multi-Model Multi-Prompt Sequences

Kavya leverages models together to create advanced multi-model, multi-prompt sequences. This allows for:

- **Dynamic Prompt Routing**: Automatically select the most suitable AI model based on the task.
- **Chained Prompts**: Break down complex tasks into manageable steps, improving output quality.
- **Context Preservation**: Maintain context across multiple prompts for coherent content generation.
- **Custom Workflows**: Design custom sequences tailored to your specific content creation needs.

### How to Use Advanced Features

1. **Enable Advanced Mode**
   - In your project settings, toggle on **Advanced Mode**.

2. **Configure Your Workflow**
   - Use the visual editor to arrange and configure prompt sequences.
   - Select from available AI models and tools.

3. **Generate Content**
   - Run the workflow to generate content.
   - Review and adjust as needed.

---

## Security and Compliance

### European Hosting

- **Data Centers**: All data is stored and processed on servers located within the European Union.
- **Data Residency**: Ensures compliance with local data residency requirements.

### EU Data Security and Privacy Standards

- **GDPR Compliance**: Kavya adheres to the General Data Protection Regulation (GDPR), ensuring user data is handled with the highest level of privacy and security.
- **Data Encryption**: All data in transit and at rest is encrypted using industry-standard encryption protocols.
- **Access Controls**: Strict access controls are in place to prevent unauthorized access to user data.

### Regular Audits

- **Security Audits**: Regular third-party security assessments are conducted to ensure compliance and identify potential vulnerabilities.
- **Compliance Certifications**: Working towards obtaining relevant certifications like ISO 27001.

---

## API Documentation

Integrate Kavya's capabilities into your own applications using our RESTful API.

### Authentication

Authenticate using API keys:

- Obtain your API key from your account dashboard.
- Include it in your request headers.

```http
Authorization: Bearer YOUR_API_KEY
```

### Endpoints

- **Generate Content**

  ```http
  POST /v1/chat/completions
  ```

  Generate content with optional token chunking to control text editor refresh rate:

  ```json
  {
    "model": "gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Write an article about AI"
      }
    ],
    "stream": true,
    "chunking": {
      "chunk_size": 3
    },
    "providers": "openai,anthropic,mistral"
  }
  ```

  Parameters:
  - `model`: The model to use for generation
  - `messages`: Array of message objects with role and content
  - `stream`: Boolean to enable streaming responses
  - `chunking`: (Optional) Configuration for token chunking
    - `chunk_size`: Number of tokens to accumulate before sending (default: 3)
  - `providers`: (Optional, only valid with `kavya-m1` model) Comma-separated list of provider names to use as fallbacks

  The chunking configuration helps reduce text editor refresh rate by accumulating tokens before sending them to the client. A larger chunk size means fewer but larger updates, while a smaller size provides more granular updates.

  #### Using the Providers Parameter

  The `providers` parameter allows you to specify which LLM providers to use when making requests with the `kavya-m1` model:

  ```json
  {
    "model": "kavya-m1",
    "messages": [
      {
        "role": "user",
        "content": "Write an article about AI"
      }
    ],
    "providers": "openai,anthropic,mistral"
  }
  ```

  Supported providers:
  - `openai`: Uses gpt-4o, gpt-4o-mini
  - `anthropic`: Uses Claude 3.7 Sonnet, Claude 3 Sonnet
  - `mistral` or `mistralai`: Uses Mistral Large, Mistral Medium
  - `google` or `gemini`: Uses Gemini 2.0 Flash
  - `groq`: Uses Llama3 70B
  - `xai`: Uses Grok-2-latest

  The system will use the first model from the first provider as the primary model, with subsequent models as fallbacks. This gives you control over which models are used and in what order.

---

## Configuration

### Environment Variables (.env file)

Kavya uses a local PostgreSQL database. The required environment variables are:

```bash
# Database Configuration
DATABASE_URL=postgresql://kavya_user:postgres@localhost:5432/kavya_db

# JWT Configuration (required)
JWT_PUBLIC_KEY_B64=<base64-encoded-public-key>

# Optional: Environment setting
ENVIRONMENT=dev
```

### Configuration File

The main configuration is in `config.yaml`. This file contains:
- Model configurations
- Provider settings
- Server settings
- Database connection pooling options

---

## Contributing

We welcome contributions from the community. Please read our [Contribution Guidelines](CONTRIBUTING.md) for more information on how to contribute to Kavya.

---

## License

Kavya is licensed under the MIT License. See [LICENSE](LICENSE) for more information.

---

## Contact

For any questions or support, please contact us at [contact@kavya.com](mailto:contact@kavya.com).

---